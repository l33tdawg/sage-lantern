"""Real local cap/provision/signature controls with modeled host authority only."""
from copy import deepcopy
from dataclasses import asdict,replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

import pytest

from meshticulum.contracts.identity import PeerID
from meshticulum.resources import resource,lamplighter as ll
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,UnitCaps,Cost,ParentCaps,child_caps,ParentCapsError,canonical
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,_Public32Approval
from lantern_meshticulum.bench.pilot_provision import provision
from lantern_meshticulum.bench.pilot_context import original_context
from lantern_meshticulum.bench.pilot_resources import serve_chunk,verify_chunk
from lantern_meshticulum.ip.node import _write
from tests.test_public32_service import large_pack,response
from tests.test_pilot_parent_snapshot import root_observation

BOOT='00000000-0000-4000-8000-000000000001'
PEERS={u:dict(chain_id='public32-source',agent_id=c*64) for u,c in zip(('unit-a','unit-b','unit-c'),'abc')}
clock=lambda:(100000000,800000000)


@pytest.fixture
def tmp_path():
    # Actual ancestry protection deliberately refuses pytest's system temp.
    base=Path(tempfile.mkdtemp(prefix='.public32-control-',dir=Path(__file__).parent))
    try:yield base
    finally:shutil.rmtree(base)


@pytest.fixture(autouse=True)
def protected_source(tmp_path,monkeypatch,synthetic_a658_capture):
    """Only installed location is modeled; the real Runtime checks all files."""
    from lantern_meshticulum.bench import pilot_native_caps as native,pilot_resume_rpc as rpc
    import meshticulum
    monkeypatch.setitem(globals(),"a658_capture_fixture",synthetic_a658_capture)
    core=Path(meshticulum.__file__).parent;application=Path(native.__file__).parents[1]
    runtime=tmp_path/('runtime-'+'7'*16);runtime.mkdir(mode=0o700)
    sources={}
    for package in ('bench','contracts','scheduler','flow','ip','meshticulum_contracts','meshticulum_scheduler','resources'):
        for path in (core/package).glob('*.py'):
            if path.name!='flash_app.py':sources['meshticulum/'+package+'/'+path.name]=path.read_bytes()
    for package in ('bench','ip'):
        for path in (application/package).glob('*.py'):sources['lantern_meshticulum/'+package+'/'+path.name]=path.read_bytes()
    sources['meshticulum/__init__.py']=(core/'__init__.py').read_bytes()
    sources['lantern_meshticulum/__init__.py']=(application/'__init__.py').read_bytes()
    core_root=core.parents[1];lantern_root=application.parents[3]
    for name in ('LICENSE','NOTICE','docs/THIRD_PARTY.md','docs/EXTRACTION.md',
                 'provenance/UPSTREAM_SOURCE.json','provenance/THIRD_PARTY_SOURCE_AUDIT.json'):
        sources[name]=(core_root/name).read_bytes()
    sources['bench_node.py']=(lantern_root/'firmware/meshticulum-bench/bench_node.py').read_bytes()
    sources['provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json']=(lantern_root/'software/meshticulum-integration/meshticulum-source-lock.json').read_bytes()
    for name,raw in sources.items():
        target=runtime/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);target.parent.chmod(0o700);_write(target,raw)
    manifest=canonical(dict(schema='meshticulum.bench-runtime-bundle.v1',
        private_services_enabled=False,public_parent_claims_required=True,
        files={name:hashlib.sha256(raw).hexdigest() for name,raw in sources.items()}))
    _write(runtime/'manifest.json',manifest)
    # Execute the ACTUAL Runtime constructor and Reader. Its fixed deployment
    # prefix and imported module placement alone are mapped to this local tree.
    monkeypatch.setattr(rpc,'Path',lambda value:tmp_path if value=='/opt/meshticulum-bench' else Path(value))
    monkeypatch.setattr(rpc,'__file__',str(runtime/'lantern_meshticulum/bench/pilot_resume_rpc.py'))
    monkeypatch.setattr(native,'__file__',str(runtime/'lantern_meshticulum/bench/pilot_native_caps.py'))
    return runtime,hashlib.sha256(manifest).hexdigest()


@pytest.mark.parametrize('fault',[None,'schema_absent','schema_unknown','private_services',
                                 'parent_claims','members_below','members_above'])
def test_protected_directory_runtime_requires_explicit_public_manifest(protected_source,fault):
    from lantern_meshticulum.bench import pilot_resume_rpc as rpc
    root,pin=protected_source
    if fault is None:
        with rpc.Runtime(root,pin) as runtime:
            assert runtime.manifest['schema']=='meshticulum.bench-runtime-bundle.v1'
            assert 75<=len(runtime.manifest['files'])<=150 and runtime.check()
        return
    path=root/'manifest.json';manifest=json.loads(path.read_bytes())
    if fault=='schema_absent':manifest.pop('schema')
    elif fault=='schema_unknown':manifest['schema']='meshticulum.bench-runtime-bundle.unknown'
    elif fault=='private_services':manifest['private_services_enabled']=True
    elif fault=='parent_claims':manifest['public_parent_claims_required']=False
    elif fault=='members_below':manifest['files']=dict(list(manifest['files'].items())[:74])
    else:manifest['files']={'uninstalled-%d.py'%i:'0'*64 for i in range(151)}
    raw=canonical(manifest);path.write_bytes(raw)
    with pytest.raises(RuntimeError,match='wrong_public_runtime'):
        rpc.Runtime(root,hashlib.sha256(raw).hexdigest())


def protected_records(root,p,unit):
    from lantern_meshticulum.bench import resume_parent_guard as guard
    from lantern_meshticulum.bench.history_identity_seal import capture as strict_capture
    from lantern_meshticulum.bench.legacy_grant_fence import capture as legacy_capture
    from lantern_meshticulum.bench import pilot_native_caps as native
    from lantern_meshticulum.bench.profiles import profile_for
    for name in ('parents','runs','campaigns'):(root/name).mkdir(mode=0o700)
    for old in guard.HISTORY:(root/'parents'/old).mkdir(mode=0o700)
    old=root/'runs'/('e'*32);old.mkdir(mode=0o700)
    legacy_grant=dict(schema='meshticulum.bench-run.v1',unit=unit,session_id='e'*32,host_boot_id=BOOT,
        issued_epoch_us=1,expires_epoch_us=2,profile=dict(max_boot_attempts=256,max_boot_estimated_airtime_us=60000000))
    raw=canonical(legacy_grant);_write(old/'grant.json',raw);_write(old/'active',b'retired\n')
    rows=[dict(run_id='e'*32,schema=legacy_grant['schema'],grant_sha256=hashlib.sha256(raw).hexdigest(),
        host_boot_id=BOOT,original_issued_epoch_us=1,original_expires_epoch_us=2,active_marker='retired',
        caps=dict(radio_attempts=256,radio_estimated_airtime_us=60000000,ip_frames=0,tx_stream_bytes=0,rx_stream_bytes=0))]
    legacy,_=legacy_capture(root/'runs',owner_uid=os.geteuid(),expected_rows=rows,
        independently_verified=True,exact_namespace_gate=lambda:list((root/'runs').iterdir())==[old])
    directories={'':['parents','runs','campaigns'],'parents':list(guard.HISTORY),'runs':['e'*32]}
    directories.update({'parents/'+old:[] for old in guard.HISTORY})
    extra=a658_capture_fixture(root,unit,clock,boot=BOOT)
    files={}
    if extra is not None:
        from lantern_meshticulum.bench.capture_a658 import PARENT
        directories['parents'].append(PARENT)
        prefix='parents/'+PARENT
        directories[prefix]=list(extra['files'])+['prepared-identities']
        directories[prefix+'/prepared-identities']=[]
        files.update({prefix+'/'+name:pin for name,pin in extra['files'].items()})
    from lantern_meshticulum.bench import capture_8120
    extra=capture_8120.row(unit);prefix='parents/'+capture_8120.PARENT
    directories['parents'].append(capture_8120.PARENT)
    directories[prefix]=list(extra['files'])+['prepared-identities'];directories[prefix+'/prepared-identities']=[]
    files.update({prefix+'/'+name:pin for name,pin in extra['files'].items()})
    strict,_=strict_capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)
    directory=root/'parents'/p.parent_id;directory.mkdir(mode=0o700)
    _,binding=guard.install_seals(root,p.parent_id,unit,strict,legacy,clock=clock)
    prior=dict(history_binding_sha256=binding,full_caps=asdict(p.unit(unit).prior_debt),legacy_inventory=rows,
        complete_global_debt=False,actual_cost=None,refunds=0)
    prior_sha=hashlib.sha256(canonical(prior)).hexdigest()
    registry={u:dict(observation_sha256=prior_sha) for u in PEERS}
    p=replace(p,units=tuple(replace(u,prior_snapshot_sha256=prior_sha) for u in p.units),
        prior_snapshot_sha256=hashlib.sha256(canonical(registry)).hexdigest())
    phase=dict(epoch_us=clock()[0],monotonic_us=clock()[1],deadline_monotonic_us=1105000000)
    capture=dict(unit=unit,parent_id=p.parent_id,actual_history_verified=True,history_binding_sha256=binding,
        review_sha256='6'*64,facts={k:facts(unit)[k] for k in ('unit','host_boot_id','services','service_lifetimes')},
        native_phase=phase,phase_start_epoch_us=p.issued_epoch_us,phase_end_epoch_us=p.expires_epoch_us+5000000,
        history=dict(previous=[dict(paid_caps=asdict(p.unit(unit).prior_debt))],legacy_inventory=rows))
    runtime=Path(native.__file__).parents[2];manifest=(runtime/'manifest.json').read_bytes()
    owner=dict(schema='meshticulum.finite-public-resume-owner.v1',qualification_kind='c-bulk32-v1',
        policy_digest=p.digest,manifest_sha256=hashlib.sha256(manifest).hexdigest(),runtime_sha256='7'*64,
        capture_record_sha256=hashlib.sha256(canonical(capture)).hexdigest(),history_binding_sha256=binding,
        review_sha256='6'*64,prior_registry=registry,facts={unit:capture['facts']},native_phase={unit:phase},
        phase_start_epoch_us=capture['phase_start_epoch_us'],phase_end_epoch_us=capture['phase_end_epoch_us'],
        authorization=dict(human_statement='run it bro',count_authorized=1,public_only=True,private_production=False),
        rf_record_sha256='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9',
        rf_record=dict(country='MY',C_transmission_allowed=True,A_B_transmission_allowed=True,
            units={u:dict(antenna_connected=True) for u in PEERS}),rf_profile=asdict(profile_for('sf7-500')))
    for name,value in [('prior.json',prior),('policy.json',p.to_dict()),('capture.json',capture),('owner.json',owner)]:
        _write(directory/name,canonical(value))
    return p,directory,binding


def policy(kind='c-bulk32-v1'):
    prior=Cost(3,10000)
    units=tuple(UnitCaps(u,BOOT,PeerID(**PEERS[u]),prior if u=='unit-b' else prior.plus(Cost(512,120000000)),
        prior,c*64) for u,c in zip(PEERS,'abc'))
    total=Cost()
    for u in units:total=total.plus(u.limits)
    return ParentPolicy('1'*32,BOOT,99000000,400000000,800000000,1101000000,units,total,'d'*64,kind)


def pair(pack,fmt):
    request,reply=response(pack);p=policy()
    payload=request if fmt=='MR1.request' else reply
    sender,receiver=('unit-a','unit-c') if fmt=='MR1.request' else ('unit-c','unit-a')
    values=pair_packages(campaign_id='c'*32,resource_sha256=pack[2],application_expires_epoch_us=180000000,
        sender=sender,receiver=receiver,transport='lora',format=fmt,
        descriptor=dict(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest()),
        source=None if fmt=='MR1.request' else dict(kind='served_public_chunk',request_run_id='2'*32),
        request=request if fmt=='MR1.request' else None,peers=PEERS,boots={u:BOOT for u in PEERS},
        now_epoch_us=100000000,radio_permission=dict(country='MY',antenna_confirmed=True),public32=True)
    return p,values


def enroll(root,p,values,unit):
    root.mkdir(mode=0o700)
    p,directory,binding=protected_records(root,p,unit)
    control=root.parent/(root.name+'-controller');control.mkdir(mode=0o700)
    owner=ParentCaps.enroll(control/'caps.sqlite',p,clock=clock,boot_reader=lambda:BOOT)
    try:
        permit=owner.prepay_pair(list(values['packages'].values()));children=owner.consume(permit)
        child=next(c for c in children if c.unit==unit)
        package=deepcopy(values['packages'][unit]);package['schema']='meshticulum.public-pilot-package.v2'
        package['parent']=dict(schema='meshticulum.public-parent-allocation.v1',parent_id=p.parent_id,
            policy_digest=p.digest,allocation_id=permit.key,child=child.to_dict())
    finally:owner.close()
    approve=_Public32Approval(directory,p,unit,facts(unit),clock)
    def prior_checked(raw):
        return json.loads(raw)==dict(parent_id=p.parent_id,policy_digest=p.digest,unit=asdict(p.unit(unit)),
            prior_snapshot_sha256=p.prior_snapshot_sha256)
    native=NativeCaps.enroll(directory/'native.sqlite',p,unit=unit,clock=clock,
        facts=lambda:NativeFacts(unit,BOOT,p.unit(unit).peer),approve=approve,prior_check=prior_checked,
        history_binding_sha256=binding)
    assert native.close()
    return package,directory,p


def facts(unit):return dict(unit=unit,host_boot_id=BOOT,epoch_us=clock()[0],monotonic_us=clock()[1],
    services={},service_lifetimes={})


def capture(root,run,payload,fmt,digest,unit='unit-c'):
    d=root/'runs'/run;d.mkdir(parents=True,mode=0o700);d.parent.chmod(0o700)
    plan=dict(service=dict(kind='knowledge',format=fmt,public_response_max_bytes=32768),
        object=dict(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest()))
    canonical=json.dumps(plan,sort_keys=True,separators=(',',':')).encode()
    result=dict(schema='meshticulum.physical-service-run.v1',unit=unit,host_boot_id=BOOT,
        complete=True,sends=False,service_format=fmt,service_object_durable=True,
        service_object_sha256=hashlib.sha256(payload).hexdigest(),original_application_expires_epoch_us=180000000,
        original_application_deadline_us=880000000,parent=dict(native_plan_sha256=hashlib.sha256(canonical).hexdigest(),
            resource_sha256=digest))
    for name,raw in [('grant.json',canonical),('result.json',json.dumps(result).encode()),
                     ('receiver.bin',payload),('active',b'active\n')]:_write(d/name,raw)
    return d


def test_legacy_policy_bytes_and_digest_omit_new_kind():
    legacy=policy(None);body=legacy.to_dict();assert 'qualification_kind' not in body
    assert ParentPolicy.from_dict(body).digest==legacy.digest
    new=policy();assert new.to_dict()['qualification_kind']=='c-bulk32-v1'
    assert ParentPolicy.from_dict(new.to_dict()).digest==new.digest!=legacy.digest


def test_native_kind_binds_exact32k_public_C_to_A_costs_and_refuses_old_parent(large_pack):
    p,values=pair(large_pack,'MR1.response')
    for unit in ('unit-a','unit-c'):
        child=child_caps(values['packages'][unit],p)
        assert child.cost==Cost(256,60000000)
        assert json.loads(child.metadata)['object']['bytes']==32768
        with pytest.raises(ParentCapsError):child_caps(values['packages'][unit],replace(p,qualification_kind=None))
    assert p.unit('unit-b').limits==p.unit('unit-b').prior_debt


@pytest.mark.parametrize('fault',['no_marker','private','larger','short','wrong_kind','other_waveform'])
def test_native_public32_refusals_do_not_change_paid_maxima(large_pack,fault):
    p,values=pair(large_pack,'MR1.response');packet=deepcopy(values['packages']['unit-c'])
    if fault=='no_marker':packet['grant']['service'].pop('public_response_max_bytes')
    if fault=='private':packet['grant']['service'].update(kind='protected_message',format='LME.binary')
    if fault=='larger':packet['grant']['object']['bytes']=32769
    if fault=='short':packet['grant']['object']['bytes']=32767
    if fault=='wrong_kind':p=replace(p,qualification_kind=None)
    if fault=='other_waveform':packet['grant']['profile']['bandwidth_hz']=250000
    with pytest.raises(ParentCapsError):child_caps(packet,p)


def test_native_provision_sender_has32k_marker_and_request_and_keeps_caps(tmp_path,large_pack):
    p,values=pair(large_pack,'MR1.request');root=tmp_path/'native'
    packet,parent,p=enroll(root,p,values,'unit-a')
    receipt=provision(packet,state=root,facts=facts('unit-a'),clock=clock)
    assert receipt['parent']['prepared'] and not receipt['radio_opened']
    assert (root/'runs'/receipt['run_id']/'sender.bin').read_bytes()==response(large_pack)[0]
    native=NativeCaps.open(parent/'native.sqlite',p,unit='unit-a',clock=clock,
        facts=lambda:NativeFacts('unit-a',BOOT,p.unit('unit-a').peer),approve=lambda _:True)
    try:
        snap=native.snapshot();assert snap['claims']==snap['prepared']==1 and snap['starts']==0
        assert snap['debt']==asdict(Cost(259,60010000))
    finally:native.close()


def test_failed_response_source_retains_full_native_prepaid_debt_once(tmp_path,large_pack):
    p,values=pair(large_pack,'MR1.response');root=tmp_path/'native'
    packet,parent,p=enroll(root,p,values,'unit-c')
    with pytest.raises(FileNotFoundError):provision(packet,state=root,facts=facts('unit-c'),clock=clock)
    native=NativeCaps.open(parent/'native.sqlite',p,unit='unit-c',clock=clock,
        facts=lambda:NativeFacts('unit-c',BOOT,p.unit('unit-c').peer),approve=lambda _:True)
    try:
        snap=native.snapshot();assert snap['claims']==1 and snap['prepared']==snap['starts']==0
        assert snap['debt']==asdict(Cost(259,60010000))
    finally:native.close()
    # Retry after releasing/reopening the actual owner: a lock refusal alone
    # would not establish durable one-time charging.
    with pytest.raises(Exception):provision(packet,state=root,facts=facts('unit-c'),clock=clock)
    native=NativeCaps.open(parent/'native.sqlite',p,unit='unit-c',clock=clock,
        facts=lambda:NativeFacts('unit-c',BOOT,p.unit('unit-c').peer),approve=lambda _:True)
    try:assert native.snapshot()==snap
    finally:native.close()


def test_full_signed32k_native_capture_serving_and_publisher_verification(tmp_path,large_pack):
    publisher=tmp_path/'publisher';publisher.mkdir(mode=0o700)
    recipient=tmp_path/'recipient-state';recipient.mkdir(mode=0o700)
    destination=tmp_path/'recipient-cache';destination.mkdir(mode=0o700)
    request,reply=response(large_pack)
    for root,unit in ((publisher,'unit-c'),(recipient,'unit-a')):
        context=root/'campaigns'/('c'*32);context.mkdir(parents=True,mode=0o700);context.parent.chmod(0o700)
        original_context(context,unit=unit,host_boot_id=BOOT,resource_sha256=large_pack[2],
            expires_epoch_us=180000000,clock=clock)
    capture(publisher,'2'*32,request,'MR1.request',large_pack[2])
    assert serve_chunk(publisher,'2'*32,facts=facts('unit-c'),clock=clock,seed=large_pack[0],
        trust=large_pack[1],manifest_sha256=large_pack[2],public32=True)==reply
    capture(recipient,'3'*32,reply,'MR1.response',large_pack[2],unit='unit-a')
    for name in ('manifest.json','manifest.sig.json'):_write(destination/name,(large_pack[0]/name).read_bytes())
    value=verify_chunk(recipient,'3'*32,facts=facts('unit-a'),clock=clock,request=request,campaign_id='c'*32,
        manifest_sha256=large_pack[2],destination=destination,trust=large_pack[1],public32=True)
    assert value['publisher_verified'] and value['useful_verified_bytes']==32740 and not value['reused']
    assert ll.VerifiedCache(destination,large_pack[1],large_pack[2]).piece(value['chunk_sha256'])==large_pack[4]


def test_native_response_provision_uses_signed_store_not_operator_body(tmp_path,large_pack,root_observation):
    p,values=pair(large_pack,'MR1.response');root=tmp_path/'native'
    packet,parent,p=enroll(root,p,values,'unit-c')
    request,reply=response(large_pack)
    capture(root,'2'*32,request,'MR1.request',large_pack[2])
    public=root/'public'/large_pack[2];public.mkdir(parents=True,mode=0o700);public.parent.chmod(0o700)
    import shutil
    shutil.copytree(large_pack[0],public/'pack')
    _write(public/'trust.json',json.dumps(large_pack[1]).encode())
    receipt=provision(packet,state=root,facts=facts('unit-c'),clock=clock)
    assert receipt['source_basis']=='native_verified_public_store_response'
    assert (root/'runs'/receipt['run_id']/'sender.bin').read_bytes()==reply
    assert receipt['parent']['prepared'] and not receipt['radio_opened']
    assert json.loads((root/'runs'/receipt['run_id']/'grant.json').read_bytes())['service']['public_response_max_bytes']==32768
    # ACTUAL typed collectors, after real enroll/prepay/provision and closure.
    from lantern_meshticulum.bench.pilot_parent_snapshot import Scope,collect
    observed={}
    for role,directory,name,unit in [('controller',root.parent/(root.name+'-controller'),'caps.sqlite',None),
                                      ('native',root,'native.sqlite','unit-c')]:
        db=directory/name if role=='controller' else parent/name
        scope=Scope('meshticulum.public-parent-snapshot-scope.v1',role,os.geteuid(),p.parent_id,p.digest,
            hashlib.sha256(db.read_bytes()).hexdigest(),unit,BOOT,130000000)
        observed[role]=collect(directory,scope,clock=clock,boot_reader=lambda:BOOT).to_dict()
        assert observed[role]['status']=='scoped_rows_observed',observed[role]
        assert observed[role]['scoped_rows_complete'] and observed[role]['files']
    detail=observed['native']['detail']
    assert (detail['claim_count'],detail['prepared_count'],detail['start_count'])==(1,1,0)
    assert detail['prior_plus_prepaid_caps']==asdict(Cost(259,60010000))
    assert detail['claims'][0]['child']['public_response_max_bytes']==32768
    assert detail['claims'][0]['child']['object']['bytes']==32768
    assert detail['claims'][0]['native_plan_sha256']==receipt['parent']['native_plan_sha256']
    assert observed['controller']['detail']['allocation_count']==1


def unpaid(parent,root):
    with sqlite3.connect(parent/'native.sqlite') as db:
        assert [db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('claims','prepared','starts')]==[0,0,0]
    assert not list((root/'campaigns').iterdir())
    assert sorted(p.name for p in (root/'runs').iterdir())==['e'*32]


@pytest.mark.parametrize('fault',['missing','wrong_kind','policy','manifest','review','phase','unit','prior',
    'binding','duplicate','mode','symlink','runtime_source','clock_expired','revoked','parent_replaced'])
def test_protected_owner_refuses_before_any_native_claim_or_provision(tmp_path,large_pack,protected_source,fault):
    p,values=pair(large_pack,'MR1.request');root=tmp_path/'native';packet,parent,p=enroll(root,p,values,'unit-a')
    owner=parent/'owner.json';value=json.loads(owner.read_bytes());local_clock=clock
    if fault=='missing':owner.unlink()
    elif fault=='wrong_kind':value['qualification_kind']='c-bulk-v1';owner.write_bytes(canonical(value))
    elif fault=='policy':value['policy_digest']='0'*64;owner.write_bytes(canonical(value))
    elif fault=='manifest':value['manifest_sha256']='0'*64;owner.write_bytes(canonical(value))
    elif fault=='review':value['review_sha256']='0'*64;owner.write_bytes(canonical(value))
    elif fault=='phase':value['native_phase']['unit-a']['deadline_monotonic_us']+=1;owner.write_bytes(canonical(value))
    elif fault=='unit':value['facts']['unit-a']['unit']='unit-c';owner.write_bytes(canonical(value))
    elif fault=='prior':(parent/'prior.json').write_bytes(b'{}')
    elif fault=='binding':(parent/'history-binding.json').write_bytes(b'{}')
    elif fault=='duplicate':owner.write_bytes(b'{"qualification_kind":"c-bulk32-v1",'+owner.read_bytes()[1:])
    elif fault=='mode':owner.chmod(0o644)
    elif fault=='symlink':owner.rename(parent/'saved-owner');owner.symlink_to(parent/'saved-owner')
    elif fault=='runtime_source':
        path=protected_source[0]/'meshticulum/bench/authority.py';path.write_bytes(path.read_bytes()+b'\n')
    elif fault=='clock_expired':local_clock=lambda:(406000000,1106000000)
    elif fault=='revoked':(parent/'native.sqlite.approval').write_bytes(b'revoked\n')
    elif fault=='parent_replaced':
        saved=root.parent/'retained-original-parent';parent.rename(saved);shutil.copytree(saved,parent)
    with pytest.raises(Exception):provision(packet,state=root,facts=facts('unit-a'),clock=local_clock)
    unpaid(parent,root)


@pytest.mark.parametrize('when',['before_claim','after_claim'])
def test_owner_inode_replacement_during_claim_refuses_and_preserves_paid_debt(tmp_path,large_pack,monkeypatch,when):
    p,values=pair(large_pack,'MR1.request');root=tmp_path/'native';packet,parent,p=enroll(root,p,values,'unit-a')
    original=NativeCaps.prepare_claim
    def replace():
        owner=parent/'owner.json';replacement=parent/'replacement.json';_write(replacement,owner.read_bytes())
        replacement.replace(owner)
    def claim(caps,value):
        if when=='before_claim':replace()
        result=original(caps,value)
        if when=='after_claim':replace()
        return result
    monkeypatch.setattr(NativeCaps,'prepare_claim',claim)
    with pytest.raises(Exception):provision(packet,state=root,facts=facts('unit-a'),clock=clock)
    with sqlite3.connect(parent/'native.sqlite') as db:
        rows=db.execute('SELECT body FROM claims').fetchall()
        assert len(rows)==(0 if when=='before_claim' else 1)
        if rows:assert json.loads(rows[0][0])['child']['cost']==asdict(Cost(256,60000000))
        assert db.execute('SELECT COUNT(*) FROM prepared').fetchone()==(0,)
        assert db.execute('SELECT COUNT(*) FROM starts').fetchone()==(0,)
    assert not list((root/'campaigns').iterdir())
    assert sorted(p.name for p in (root/'runs').iterdir())==['e'*32]


def test_owner_rebound_after_finalization_cannot_publish_prepared_directory(tmp_path,large_pack,monkeypatch):
    from lantern_meshticulum.bench import pilot_provision as module
    p,values=pair(large_pack,'MR1.request');root=tmp_path/'native';packet,parent,p=enroll(root,p,values,'unit-a')
    original=module._history_guarded
    def checked(state,caps):
        result=original(state,caps)
        replacement=parent/'replacement.json';_write(replacement,(parent/'owner.json').read_bytes())
        replacement.replace(parent/'owner.json');return result
    monkeypatch.setattr(module,'_history_guarded',checked)
    with pytest.raises(Exception):provision(packet,state=root,facts=facts('unit-a'),clock=clock)
    assert not (root/'runs'/packet['grant']['session_id']).exists()
    with sqlite3.connect(parent/'native.sqlite') as db:
        assert [db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('claims','prepared','starts')]==[1,1,0]
        assert json.loads(db.execute('SELECT body FROM claims').fetchone()[0])['child']['cost']==asdict(Cost(256,60000000))


@pytest.mark.parametrize('fault',['short','mismatch','expiry','revoked','publisher_revoked'])
def test_native_full_piece_verification_refuses_without_cache_commit(tmp_path,large_pack,fault):
    root=tmp_path/'state';root.mkdir(mode=0o700);destination=tmp_path/'received';destination.mkdir(mode=0o700)
    request,reply=response(large_pack)
    context=root/'campaigns'/('c'*32);context.mkdir(parents=True,mode=0o700);context.parent.chmod(0o700)
    original_context(context,unit='unit-a',host_boot_id=BOOT,resource_sha256=large_pack[2],
        expires_epoch_us=180000000,clock=clock)
    if fault=='short':reply=reply[:-1]
    if fault=='mismatch':reply=reply[:-1]+b'!'
    d=capture(root,'3'*32,reply,'MR1.response',large_pack[2],unit='unit-a')
    for name in ('manifest.json','manifest.sig.json'):_write(destination/name,(large_pack[0]/name).read_bytes())
    local_clock=(lambda:(181000000,881000000)) if fault=='expiry' else clock
    trust=deepcopy(large_pack[1])
    if fault=='publisher_revoked':trust['keys'][0]['status']='revoked'
    if fault=='revoked':(d/'active').write_bytes(b'retired\n')
    with pytest.raises(Exception):
        verify_chunk(root,'3'*32,facts=facts('unit-a'),clock=local_clock,request=request,campaign_id='c'*32,
            manifest_sha256=large_pack[2],destination=destination,trust=trust,public32=True)
    assert not (destination/'chunks').exists()
