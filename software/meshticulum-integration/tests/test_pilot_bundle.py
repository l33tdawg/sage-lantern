"""Actual signed data/protected SQLite controls; host/RF facts modeled only."""
from copy import deepcopy
from dataclasses import asdict,replace
import hashlib
import json
import os
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from meshticulum.resources import byte_bundle as bundle,knowledge_pack as kp,resource
from lantern_meshticulum.bench.pilot_bundle import BundleContext,context_value,KIND
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,Cost,ParentCaps,canonical
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,_Public32Approval
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_provision import provision
from lantern_meshticulum.bench import pilot_parent_snapshot as snapshot
from lantern_meshticulum.ip.node import _write
from tests.test_public32_pilot import tmp_path,protected_source,protected_records,clock,facts,policy,PEERS,BOOT
from tests.test_pilot_parent_snapshot import root_observation


@pytest.fixture
def data():
    payload=bytes(range(256))*390+bytes(range(160));assert len(payload)==100000
    key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    manifest,chunks,signature=bundle.build(payload,pack_id='public100k',created_at='2026-10-11T00:00:00Z',private_key=key,exact32=True)
    raw=kp.manifest_bytes(manifest);sig=kp.canonical_json(signature);digest=hashlib.sha256(raw).hexdigest()
    trust={'schema':'sage-foundation.knowledge-pack.trust.v1','keys':[{'key_id':signature['key_id'],'public_key_hex':public.hex(),'status':'active','pack_classes':['reference']}]}
    p=policy();stages=[dict(campaign_id=('%032x'%(i+2)),issued_epoch_us=(100+i*50)*1000000,
        expires_epoch_us=(140+i*50)*1000000,handoff_end_epoch_us=(150+i*50)*1000000) for i in range(5)]
    serialized={sha:kp.chunk_bytes(record) for sha,record in chunks.items()}
    value=context_value(raw,sig,trust,digest,chunks=serialized,
        policy_clock=(p.issued_epoch_us,p.original_monotonic_us,p.expires_epoch_us,p.deadline_monotonic_us),stages=stages)
    prior=Cost(3,10000);units=tuple(replace(u,limits=prior if u.unit=='unit-b' else prior.plus(Cost(2560,600000000))) for u in p.units)
    total=Cost()
    for unit in units:total=total.plus(unit.limits)
    p=replace(p,qualification_kind=KIND,bundle_context_sha256=hashlib.sha256(canonical(value)).hexdigest(),units=units,aggregate_limits=total)
    return payload,serialized,value,p


def packet(value,p,index,now):
    context=BundleContext(value,p);request,entry=context.exchange(index);stage=value['stages'][index]
    return pair_packages(campaign_id=stage['campaign_id'],resource_sha256=value['manifest_sha256'],
        application_expires_epoch_us=stage['expires_epoch_us'],sender='unit-a',receiver='unit-c',transport='lora',format='MR1.request',
        descriptor=dict(bytes=len(request),sha256=hashlib.sha256(request).hexdigest()),source=None,request=request,
        peers=PEERS,boots={u:BOOT for u in PEERS},now_epoch_us=now,radio_permission={'country':'MY','antenna_confirmed':True},public32=True)


def test_signed_whole_and_predeclared_stages_verify_decoded100k(data,tmp_path):
    payload,chunks,value,p=data;c=BundleContext(value,p);assert c.current(clock())
    path=tmp_path/'receiver';path.mkdir(mode=0o700)
    with bundle.Receiver(path,c.manifest_raw,c.signature_raw,value['trust'],value['manifest_sha256'],expires_epoch_us=value['expires_epoch_us'],clock=clock,boot_reader=lambda:BOOT) as receiver:
        for i,e in enumerate(c.expected['parts']):receiver.accept(i,chunks[e['chunk_sha256']])
        done=receiver.finish()
    assert (path/'bundle.bin').read_bytes()==payload and done['useful_decoded_bytes']==100000 and done['parts']==5
    assert done['serialized_chunk_bytes']==5*32740 and done['base64_bytes']>100000 and not done['native_transport_qualified']


@pytest.mark.parametrize('fault',['whole_sha','whole_size','extra_part','campaign_rebind','duplicate_campaign','late_stage','window_extend','wrong_signature'])
def test_wrong_whole_or_schedule_refuses_even_with_rehashed_context(data,fault):
    _,_,value,p=data;v=deepcopy(value)
    if fault=='whole_sha':v['whole_sha256']='0'*64
    if fault=='whole_size':v['whole_bytes']=100001
    if fault=='extra_part':v['stages'].append(v['stages'][-1])
    if fault=='campaign_rebind':v['stages'][0]['campaign_id']='0'*32
    if fault=='duplicate_campaign':v['stages'][1]['campaign_id']=v['stages'][0]['campaign_id']
    if fault=='late_stage':v['stages'][0]['expires_epoch_us']=v['stages'][0]['issued_epoch_us']+180000001
    if fault=='window_extend':v['expires_epoch_us']+=1
    if fault=='wrong_signature':v['signature_base64']='e30='
    p=replace(p,bundle_context_sha256=hashlib.sha256(canonical(v)).hexdigest())
    with pytest.raises(Exception):BundleContext(v,p)


def test_expired_or_unplanned_part_package_refuses_without_new_credit(data):
    _,_,value,p=data;c=BundleContext(value,p);pair=packet(value,p,0,100000000);package=pair['packages']['unit-a']
    assert c.package(package,100000000)['index']==0
    with pytest.raises(Exception):c.package(package,140000000)
    bad=deepcopy(package);bad['campaign_id']='f'*32
    with pytest.raises(Exception):c.package(bad,100000000)
    bad=deepcopy(package);bad['grant']['object']['sha256']='0'*64
    with pytest.raises(Exception):c.package(bad,100000000)
    with pytest.raises(Exception):c.current((value['expires_epoch_us'],1))


def protected_bundle(root,p,value,unit):
    root.mkdir(mode=0o700);p,directory,binding=protected_records(root,p,unit)
    owner=json.loads((directory/'owner.json').read_bytes());owner['qualification_kind']=KIND;owner['bundle_context']=value
    (directory/'owner.json').write_bytes(canonical(owner))
    approve=_Public32Approval(directory,p,unit,facts(unit),clock)
    checked=lambda raw:json.loads(raw)==dict(parent_id=p.parent_id,policy_digest=p.digest,unit=asdict(p.unit(unit)),prior_snapshot_sha256=p.prior_snapshot_sha256)
    native=NativeCaps.enroll(directory/'native.sqlite',p,unit=unit,clock=clock,facts=lambda:NativeFacts(unit,BOOT,p.unit(unit).peer),approve=approve,prior_check=checked,history_binding_sha256=binding)
    assert native.close();return p,directory


def test_two_real_protected_part_claims_keep_distinct_original_expiries_and_typed_resources(data,tmp_path,protected_source,root_observation):
    _,_,value,p=data;root=tmp_path/'native';p,directory=protected_bundle(root,p,value,'unit-a')
    controller_dir=tmp_path/'controller';controller_dir.mkdir(mode=0o700)
    now=[clock()];controller=ParentCaps.enroll(controller_dir/'caps.sqlite',p,clock=lambda:now[0],boot_reader=lambda:BOOT)
    try:
        for index in (0,1):
            now[0]=(100000000+index*50000000,800000000+index*50000000)
            pair=packet(value,p,index,now[0][0]);permit=controller.prepay_pair(tuple(pair['packages'].values()));children=controller.consume(permit)
            packet_value=deepcopy(pair['packages']['unit-a']);packet_value['schema']='meshticulum.public-pilot-package.v2'
            packet_value['parent']=dict(schema='meshticulum.public-parent-allocation.v1',parent_id=p.parent_id,policy_digest=p.digest,allocation_id=permit.key,child=next(c for c in children if c.unit=='unit-a').to_dict())
            receipt=provision(packet_value,state=root,facts=facts('unit-a'),clock=lambda:now[0]);assert receipt['parent']['prepared']
    finally:controller.close()
    with sqlite3.connect(directory/'native.sqlite') as db:
        rows=[json.loads(raw) for raw, in db.execute('SELECT body FROM resources')]
        assert len(rows)==2 and {r['application_expires_epoch_us'] for r in rows}=={140000000,190000000}
        assert {r['campaign_id'] for r in rows}=={value['stages'][0]['campaign_id'],value['stages'][1]['campaign_id']}
        assert db.execute('SELECT COUNT(*) FROM claims').fetchone()==(2,)
    db.close()
    now[0]=(200000000,900000000)
    scope=snapshot.Scope('meshticulum.public-parent-snapshot-scope.v1','native',os.geteuid(),p.parent_id,p.digest,
        hashlib.sha256((directory/'native.sqlite').read_bytes()).hexdigest(),'unit-a',BOOT,230000000)
    actual=snapshot.collect(root,scope,clock=lambda:now[0],boot_reader=lambda:BOOT).to_dict()
    assert actual['status']=='scoped_rows_observed' and actual['scoped_rows_complete'],(actual['status'],actual.get('reason'))
    assert actual['detail']['claim_count']==actual['detail']['prepared_count']==2
    assert actual['detail']['prior_plus_prepaid_caps']==asdict(Cost(515,120010000))


@pytest.mark.parametrize('field',['expected','manifest','raw','policy','manifest_raw','signature_raw'])
def test_context_derived_state_and_refs_cannot_mutate(data,field):
    _,_,value,p=data;c=BundleContext(value,p)
    if field=='expected':c.expected['parts'][0]['chunk_sha256']='0'*64
    elif field=='manifest':c.manifest['pack']['title']='Changed'
    elif field=='raw':c.raw=bytes(bytearray(c.raw))
    elif field=='policy':c.policy=replace(p)
    elif field=='manifest_raw':c.manifest_raw=bytes(bytearray(c.manifest_raw))
    else:c.signature_raw=bytes(bytearray(c.signature_raw))
    with pytest.raises(Exception):c.exchange(0)


def test_original_controller_mono_stage_bounds_do_not_refresh_from_later_start(data):
    _,_,value,p=data;c=BundleContext(value,p);stage=value['stages'][0]
    anchor=dict(original_epoch_us=p.issued_epoch_us,original_monotonic_us=p.original_monotonic_us,deadline_monotonic_us=p.deadline_monotonic_us)
    assert c.monotonic_bounds(stage,anchor)==(801000000,841000000,851000000)
    assert c.current((100000000,801000000),controller_clock=True)
    with pytest.raises(Exception):c.current((101000000,800999999),controller_clock=True)
    with pytest.raises(Exception):c.current((200000000,p.deadline_monotonic_us),controller_clock=True)


def postterminal_fixture(root,p,pair,unit,payload,local_clock):
    """Model Node's result writer after actual local provision/start. No RF proof."""
    import base64
    from lantern_meshticulum.bench.pilot_native_caps import _open_existing
    packet_value=pair['packages'][unit];run=pair['run_id']
    provision(packet_value,state=root,facts=facts(unit),clock=local_clock)
    directory=root/'runs'/run;plan=json.loads((directory/'grant.json').read_bytes())
    caps=_open_existing(root,p.parent_id,facts(unit),local_clock)
    try:start=caps.consume_start(run,plan,base64.b64decode(packet_value['secret_base64'],validate=True))
    finally:assert caps.close()
    sends=packet_value['grant']['sender_unit']==unit
    if not sends:_write(directory/'receiver.bin',payload)
    result=dict(schema='meshticulum.physical-service-run.v1',unit=unit,host_boot_id=BOOT,session_id=run,
        sends=sends,service_format=plan['service']['format'],complete=True,disarmed_on_exit=True,parent=start,
        original_application_expires_epoch_us=plan['service']['application_expires_epoch_us'],
        original_application_deadline_us=plan['application_deadline_monotonic_us'],
        service_object_durable=not sends,service_object_sha256=hashlib.sha256(payload).hexdigest())
    _write(directory/'result.json',canonical(result));return result


def native_five_part_fixture(data,tmp_path,monkeypatch,before_accept=None):
    from lantern_meshticulum.ip import node
    from lantern_meshticulum.bench.pilot_native_caps import _open_existing
    from lantern_meshticulum.bench.pilot_bundle import accept_native_part,finish_native_bundle
    payload,chunks,value,p=data;root=tmp_path/'native';p,directory=protected_bundle(root,p,value,'unit-a')
    received=root/'public'/value['manifest_sha256']/'received';received.mkdir(mode=0o700,parents=True)
    for parent in (received.parent,received.parent.parent):parent.chmod(0o700)
    # Only independent host/OS observations are modeled; Runtime/Reader and
    # actual protected owner/enrollment, provision/start records are exercised.
    monkeypatch.setattr(node,'_facts',lambda unit:facts(unit))
    control=tmp_path/'controller';control.mkdir(mode=0o700);now=[clock()];local_clock=lambda:now[0]
    controller=ParentCaps.enroll(control/'caps.sqlite',p,clock=local_clock,boot_reader=lambda:BOOT)
    records=[]
    try:
        for index,stage in enumerate(value['stages']):
            now[0]=(stage['issued_epoch_us']+1000000,801000000+index*50000000)
            c=BundleContext(value,p);request,entry=c.exchange(index);raw=chunks[entry['chunk_sha256']]
            response=resource.RESPONSE.pack(resource.MAGIC,hashlib.sha256(request).digest()[:16],0,len(raw),0)+raw
            request_pair=packet(value,p,index,now[0][0]);pairs=[request_pair]
            response_pair=pair_packages(campaign_id=stage['campaign_id'],resource_sha256=value['manifest_sha256'],
                application_expires_epoch_us=stage['expires_epoch_us'],sender='unit-c',receiver='unit-a',transport='lora',format='MR1.response',
                descriptor=dict(bytes=len(response),sha256=hashlib.sha256(response).hexdigest()),
                source=dict(kind='served_public_chunk',request_run_id=request_pair['run_id']),request=None,
                peers=PEERS,boots={u:BOOT for u in PEERS},now_epoch_us=now[0][0],radio_permission={'country':'MY','antenna_confirmed':True},public32=True)
            pairs.append(response_pair)
            for pair in pairs:
                permit=controller.prepay_pair(tuple(pair['packages'].values()));children=controller.consume(permit)
                for unit,package in pair['packages'].items():
                    package['schema']='meshticulum.public-pilot-package.v2';package['parent']=dict(schema='meshticulum.public-parent-allocation.v1',
                        parent_id=p.parent_id,policy_digest=p.digest,allocation_id=permit.key,child=next(c for c in children if c.unit==unit).to_dict())
                postterminal_fixture(root,p,pair,'unit-a',request if pair is request_pair else response,local_clock)
            caps=_open_existing(root,p.parent_id,facts('unit-a'),local_clock)
            try:
                verified=dict(schema='meshticulum.pilot-verified-piece.v1',publisher_verified=True,campaign_id=stage['campaign_id'],
                    response_run_id=response_pair['run_id'],manifest_sha256=value['manifest_sha256'],chunk_sha256=entry['chunk_sha256'])
                if before_accept is not None:before_accept(root,caps,request_pair,response_pair,index)
                result=accept_native_part(caps,state=root,facts=facts('unit-a'),campaign_id=stage['campaign_id'],request_run_id=request_pair['run_id'],
                    response_run_id=response_pair['run_id'],verified=verified,clock=local_clock)
                assert result['useful_decoded_bytes']==entry['raw_bytes'] and not result['native_transport_qualified'];records.append(result)
            finally:caps.close()
        now[0]=(350000000,1050000000);caps=_open_existing(root,p.parent_id,facts('unit-a'),local_clock)
        try:done=finish_native_bundle(caps,state=root,clock=local_clock)
        finally:assert caps.close()
        return root,p,done,records
    finally:assert controller.close()


def test_full_five_part_protected_data_composition_decodes100000_and_retains_whole_clock(data,tmp_path,protected_source,monkeypatch):
    root,p,done,records=native_five_part_fixture(data,tmp_path,monkeypatch)
    assert done['complete'] and done['useful_decoded_bytes']==100000 and done['whole_file_sha256']==hashlib.sha256(data[0]).hexdigest()
    assert done['original_start_epoch_us']==p.issued_epoch_us and done['original_start_monotonic_us']==p.original_monotonic_us
    assert done['native_receiver_timeline']['original_start_epoch_us']==100000000
    assert done['native_receiver_timeline']['original_start_monotonic_us']==800000000
    assert done['original_expires_epoch_us']==400000000 and done['original_deadline_monotonic_us']==p.deadline_monotonic_us
    assert done['native_receiver_timeline']['original_deadline_monotonic_us']==1100000000
    assert done['native_receiver_timeline']['verified_monotonic_us']==1050000000 and done['serialized_chunk_bytes']==163700
    assert done['whole_complete'] is True and 'verified_monotonic_us' not in done
    assert not done['native_transport_qualified'] and len(records)==5 and sum(r['useful_decoded_bytes'] for r in records)==100000
    assert (root/'campaigns'/p.parent_id/'bundle'/'bundle.bin').read_bytes()==data[0]
    assert not (root/'bundles').exists()


@pytest.mark.parametrize('fault',['capture_file','capture_directory','parsed_capture','foreign_fd','boot'])
def test_actual_held_capture_or_observer_rebind_refuses_before_part_publication(data,tmp_path,protected_source,monkeypatch,fault):
    from lantern_meshticulum.bench import pilot_bundle as module
    from lantern_meshticulum.ip import node
    original=bundle.Receiver._atomic;foreign=[]
    def before(root,caps,request,response,index):
        assert index==0
        if fault=='boot':monkeypatch.setattr(node,'_facts',lambda unit:dict(facts(unit),host_boot_id='00000000-0000-4000-8000-000000000002'))
        def changed(receiver,fd,name,raw):
            scope=receiver.clock.__self__;captured=scope.captures[-1]
            if fault=='capture_file':
                target=root/'runs'/response['run_id']/'result.json';value=json.loads(target.read_bytes());value['complete']=False;target.write_bytes(canonical(value))
            if fault=='capture_directory':
                target=root/'runs'/response['run_id'];saved=root/'retained-response';target.rename(saved)
                import shutil
                shutil.copytree(saved,target)
            if fault=='parsed_capture':captured.result['complete']=False
            if fault=='foreign_fd':
                other=root/'foreign';other.mkdir(mode=0o700);replacement=os.open(other,os.O_RDONLY|os.O_DIRECTORY)
                destination=captured.fd;os.close(destination);os.dup2(replacement,destination);os.close(replacement);foreign.append(destination)
            return original(receiver,fd,name,raw)
        monkeypatch.setattr(bundle.Receiver,'_atomic',changed)
    with pytest.raises(Exception):native_five_part_fixture(data,tmp_path,monkeypatch,before_accept=before)
    parts=list((tmp_path/'native'/'campaigns').rglob('parts/*.json'))
    assert not parts
    for fd in foreign:
        assert os.fstat(fd)
        os.close(fd)


def test_native_stage_monotonic_expiry_refuses_before_new_claim_even_with_live_epoch(data,tmp_path,protected_source):
    from lantern_meshticulum.bench.pilot_native_caps import _open_existing
    _,_,value,p=data;root=tmp_path/'native';p,directory=protected_bundle(root,p,value,'unit-a')
    control=tmp_path/'controller';control.mkdir(mode=0o700);pair=packet(value,p,0,101000000)
    controller=ParentCaps.enroll(control/'caps.sqlite',p,clock=clock,boot_reader=lambda:BOOT)
    try:
        permit=controller.prepay_pair(tuple(pair['packages'].values()));children=controller.consume(permit)
        packet_value=deepcopy(pair['packages']['unit-a']);packet_value['schema']='meshticulum.public-pilot-package.v2'
        packet_value['parent']=dict(schema='meshticulum.public-parent-allocation.v1',parent_id=p.parent_id,policy_digest=p.digest,
            allocation_id=permit.key,child=next(c for c in children if c.unit=='unit-a').to_dict())
    finally:assert controller.close()
    late=lambda:(101000000,840000000)
    with pytest.raises(Exception):provision(packet_value,state=root,facts=facts('unit-a'),clock=late)
    with sqlite3.connect(directory/'native.sqlite') as db:assert db.execute('SELECT COUNT(*) FROM claims').fetchone()==(0,)
    db.close()


@pytest.mark.parametrize('fault',['remove_result','remove_payload','map_rebind','entry_rebind',
    'anchor_extend','anchor_rebind','capture_deadline_extend','owner_rebind','stack_rebind','stack_callbacks_rebind','cleanup_rebind'])
def test_retained_capture_map_owner_and_original_clock_bindings_refuse_before_real_part_link(data,tmp_path,protected_source,monkeypatch,fault):
    from contextlib import ExitStack
    original=bundle.Receiver._atomic;owned=[];foreign=[];cleanup=[]
    def before(root,caps,request,response,index):
        assert index==0
        def mutate(receiver,fd,name,raw):
            scope=receiver.clock.__self__;capture=scope.captures[-1];owner=scope.owner
            owned.extend((entry[0],tuple(entry[1][:2])) for entry in capture.files.values())
            target=root/'runs'/response['run_id']
            if fault in ('remove_result','remove_payload'):
                filename='result.json' if fault=='remove_result' else 'receiver.bin'
                capture.files.pop(filename)
                if filename=='result.json':
                    value=json.loads((target/filename).read_bytes());value['complete']=False;(target/filename).write_bytes(canonical(value))
                else:
                    previous=target/filename;previous.rename(target/'saved-receiver.bin');previous.write_bytes(raw);previous.chmod(0o600)
            elif fault=='map_rebind':capture.files=dict(capture.files)
            elif fault=='entry_rebind':capture.files['result.json']=tuple(list(capture.files['result.json']))
            elif fault=='anchor_extend':owner.anchor['deadline_monotonic_us']+=1000000
            elif fault=='anchor_rebind':owner.anchor=dict(owner.anchor)
            elif fault=='capture_deadline_extend':capture.original_deadline+=1000000
            elif fault=='owner_rebind':capture.owner=object()
            elif fault=='cleanup_rebind':capture._cleanup=lambda:cleanup.append('foreign')
            elif fault=='stack_rebind':
                other=ExitStack();foreign.append(other);other.callback(cleanup.append,'foreign');capture.stack=other
            else:
                other=ExitStack();foreign.append(other);other.callback(cleanup.append,'foreign')
                capture.stack._exit_callbacks=other._exit_callbacks
                # Also mutate a consumed file, so the original stack's captured
                # cleanup still has to run on genuine rejection.
                capture.result['complete']=False
            return original(receiver,fd,name,raw)
        monkeypatch.setattr(bundle.Receiver,'_atomic',mutate)
    with pytest.raises(Exception):native_five_part_fixture(data,tmp_path,monkeypatch,before_accept=before)
    assert not list((tmp_path/'native'/'campaigns').rglob('parts/*.json'))
    assert not cleanup
    for fd,identity in owned:
        try:current=os.fstat(fd)
        except OSError:continue
        assert (current.st_dev,current.st_ino)!=identity,'original_capture_descriptor_leaked'
    for stack in foreign:stack.close()


def test_actual_five_verified_parts_status_does_not_claim_published_whole_file(data,tmp_path,protected_source,monkeypatch):
    from lantern_meshticulum.bench import pilot_bundle as module
    from lantern_meshticulum.ip import node
    from pathlib import Path
    root,p,done,records=native_five_part_fixture(data,tmp_path,monkeypatch)
    (root/'campaigns'/p.parent_id/'bundle/bundle.bin').unlink()
    original_path=Path
    monkeypatch.setattr(module,'Path',lambda value:root if value=='/var/lib/meshticulum-bench' else original_path(value))
    monkeypatch.setattr(node,'clock',lambda:(350000000,1050000000))
    status=module.native_action('unit-a',p.parent_id,'status')
    assert status['verified_parts']==list(range(5)) and status['all_parts_present'] is True
    assert status['whole_complete'] is False and 'complete' not in status
    assert not (root/'campaigns'/p.parent_id/'bundle/bundle.bin').exists()


def test_copied_deadline_cannot_accept_after_genuine_resource_mono_expiry(data,tmp_path,protected_source,monkeypatch):
    original=bundle.Receiver._atomic
    def before(root,caps,request,response,index):
        assert index==0
        def expire(receiver,fd,name,raw):
            scope=receiver.clock.__self__;capture=scope.captures[-1];owner=scope.owner
            # Move only modeled clock state; all native policy/enrollment and
            # actual protected provision/start/file records remain untouched.
            clock_state=owner.clock.__closure__[0].cell_contents
            original_deadline=capture.original_deadline
            epoch,_=clock_state[0];clock_state[0]=(epoch,original_deadline)
            assert original_deadline<owner.anchor['deadline_monotonic_us']
            capture.original_deadline+=10000000
            return original(receiver,fd,name,raw)
        monkeypatch.setattr(bundle.Receiver,'_atomic',expire)
    with pytest.raises(Exception):native_five_part_fixture(data,tmp_path,monkeypatch,before_accept=before)
    assert not list((tmp_path/'native'/'campaigns').rglob('parts/*.json'))


@pytest.mark.parametrize('filename',['active','result.json'])
def test_same_inode_open_description_replacement_refuses_and_cleanup_preserves_foreign_fd(data,tmp_path,protected_source,monkeypatch,filename):
    original=bundle.Receiver._atomic;foreign=[]
    def before(root,caps,request,response,index):
        assert index==0
        def replace(receiver,fd,name,raw):
            capture=receiver.clock.__self__.captures[-1]
            destination=capture.files[filename][0];old=os.fstat(destination)
            os.close(destination)
            replacement=os.open(root/'runs'/response['run_id']/filename,os.O_RDONLY|os.O_NOFOLLOW)
            if replacement!=destination:os.dup2(replacement,destination);os.close(replacement)
            current=os.fstat(destination)
            assert (old.st_dev,old.st_ino)==(current.st_dev,current.st_ino)
            assert os.lseek(destination,0,os.SEEK_CUR)==0
            foreign.append(destination)
            return original(receiver,fd,name,raw)
        monkeypatch.setattr(bundle.Receiver,'_atomic',replace)
    try:
        with pytest.raises(Exception):native_five_part_fixture(data,tmp_path,monkeypatch,before_accept=before)
        assert not list((tmp_path/'native'/'campaigns').rglob('parts/*.json'))
        assert len(foreign)==1 and os.fstat(foreign[0])
    finally:
        for fd in foreign:os.close(fd)
