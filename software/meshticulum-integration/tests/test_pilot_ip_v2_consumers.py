"""Explicit v2 source consumer controls; no device/backend qualification."""
from dataclasses import asdict,replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

from meshticulum.bench.services import resource
from meshticulum.bench.wire import WireError
from meshticulum.ip.wire import READY_MODE
from lantern_meshticulum.bench import pilot_debt_snapshot as debt, pilot_job as job, pilot_resume_rpc as rpc
from lantern_meshticulum.bench import legacy_grant_fence as fence
from lantern_meshticulum.bench.pilot_ip_schema import ip_metadata_grant
from lantern_meshticulum.bench.pilot_controller import Controller,pair_packages
from lantern_meshticulum.bench.pilot_parent_caps import child_caps,canonical
from tests import test_pilot_controller as control
from tests.test_pilot_controller import release_model_parents
from tests import test_ip_node as node_fixture
from tests.test_pilot_provision_identity import setup as native_preparation
from tests.test_pilot_provision_identity import run as provision_original
from tests.test_pilot_debt_snapshot import fixture,collect,before_after,root_observation,write,BOOT,RUN,PEERS


def ready_plan(tmp_path):
    return fixture(tmp_path,startup_mode=READY_MODE)


@pytest.mark.parametrize('damage',['v1_extra','v2_missing','unknown','extra','limits_missing'])
def test_ip_metadata_schema_and_mode_are_exact(tmp_path,damage):
    _,_,p,_=ready_plan(tmp_path)
    if damage=='v1_extra':p['schema']='meshticulum.ip-service-run.v1'
    elif damage=='v2_missing':p.pop('startup_mode')
    elif damage=='unknown':p['startup_mode']='another_mode'
    elif damage=='extra':p['accepted']=True
    else:p['limits'].pop('max_frames')
    with pytest.raises((WireError,TypeError)):ip_metadata_grant(p)


@pytest.mark.parametrize('state,written',[('KERNEL_ACCEPTED',83),('NOT_SENT',0),('UNKNOWN',10),('RESERVED',0)])
def test_ready_debt_counts_all_83_bytes_without_replacing_old_attempts(tmp_path,root_observation,state,written):
    base=tmp_path/'legacy';base.mkdir();_,_,_,old_scope=fixture(base)
    fresh=tmp_path/'ready';fresh.mkdir();root,directory,p,scope=ready_plan(fresh)
    with sqlite3.connect(directory/'bytes.sqlite') as db:
        db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',(5,b'e'*32,83,'READY',written,state))
    db.close()
    with sqlite3.connect(directory/'wire.sqlite') as db:db.execute('UPDATE state SET tx=5')
    db.close()
    before=before_after(directory);view=collect(root,scope);row=view['runs'][0]
    assert row['status']=='scoped_rows_observed' and row['grant']['startup_mode']==READY_MODE,row
    assert row['grant']['context_sha256']==ip_metadata_grant(p).digest.hex()!=old_scope.runs[0].grant_context_sha256
    assert row['actual_cost']==dict(radio_attempts=0,estimated_airtime_us=0,ip_frames=5,tx_stream_bytes=663,rx_stream_bytes=210)
    assert row['journal']['attempts'][-1]['reserved_bytes']==83
    assert len(row['journal']['attempts'])==5 and row['journal']['has_unknown_or_pending']
    assert row['admitted_caps']==row['conservative_unknown_caps']
    assert not view['eligible_for_enrollment'] and not row['parent_prepayment_verified']
    assert before_after(directory)==before


@pytest.mark.parametrize('damage',['v1','size','receiver'])
def test_ready_lane_cannot_be_claimed_by_legacy_or_wrong_direction(tmp_path,root_observation,damage):
    root,directory,p,scope=fixture(tmp_path,startup_mode=None if damage=='v1'else READY_MODE)
    if damage=='receiver':
        p['local_peer']=asdict(PEERS[1]);write(directory/'grant.json',p)
        scope=replace(scope,runs=(replace(scope.runs[0],grant_sha256=debt.sha((directory/'grant.json').read_bytes())),))
    with sqlite3.connect(directory/'bytes.sqlite') as db:
        db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',(5,b'e'*32,82 if damage=='size'else 83,'READY',0,'UNKNOWN'))
    db.close()
    row=collect(root,scope)['runs'][0]
    assert row['status']=='unavailable' and row['actual_cost'] is None
    assert row['conservative_unknown_caps'] is not None


def test_ready_retirement_binds_native_plan_context_and_preserves_debt(tmp_path):
    _,directory,p,_=ready_plan(tmp_path);write(directory/'active',b'active\n')
    grant=ip_metadata_grant(p)
    parent=dict(schema='meshticulum.native-parent-claim.v1',prepared=True,unit='unit-a',host_boot_id=BOOT,
        run_id=RUN,native_plan_sha256=debt.sha(canonical(p)),grant_context_sha256=grant.digest.hex())
    provision=dict(schema='meshticulum.provisioned-public-pilot.v2',unit='unit-a',run_id=RUN,transport='tcp',parent=parent)
    write(directory/'provision.json',provision)
    before=(directory/'bytes.sqlite').read_bytes()
    result=job.retire(directory,unit='unit-a',run_id=RUN,boot=BOOT)
    assert result['retired'] and not result['debt_refunded']
    assert (directory/'bytes.sqlite').read_bytes()==before
    assert (directory/'active').read_bytes()==b'retired\n'
    write(directory/'active',b'active\n');parent['grant_context_sha256']='0'*64;write(directory/'provision.json',provision)
    with pytest.raises(WireError):job.retire(directory,unit='unit-a',run_id=RUN,boot=BOOT)
    assert (directory/'active').read_bytes()==b'active\n'


def test_v2_legacy_metadata_fence_keeps_full_caps_and_never_reads_sqlite():
    base=Path(tempfile.mkdtemp(prefix='.ip-v2-fence-',dir=Path(__file__).parent))
    try:
        _,directory,p,_=ready_plan(base)
        raw=(directory/'grant.json').read_bytes()
        row=dict(run_id=RUN,schema=p['schema'],grant_sha256=debt.sha(raw),host_boot_id=BOOT,
            original_issued_epoch_us=p['issued_epoch_us'],original_expires_epoch_us=p['expires_epoch_us'],active_marker='retired',
            caps=dict(radio_attempts=0,radio_estimated_airtime_us=0,ip_frames=p['limits']['max_frames'],
                tx_stream_bytes=p['limits']['max_tx_stream_bytes'],rx_stream_bytes=p['limits']['max_rx_stream_bytes']))
        gate=lambda:list(directory.parent.iterdir())==[directory]
        seal,pin=fence.capture(directory.parent,owner_uid=os.geteuid(),expected_rows=[row],independently_verified=True,
            exact_namespace_gate=gate)
        with fence.LegacyGrantFence(directory.parent,seal_bytes=seal,expected_sha256=pin,owner_uid=os.geteuid(),expected_rows=[row],
            independently_verified=True,exact_namespace_gate=gate) as held:
            before=before_after(directory);held.check();assert before_after(directory)==before
    finally:shutil.rmtree(base)


def test_controller_opt_in_keeps_original_graph_parent_caps_and_sources():
    clock=control.Clock();operator=control.ModelOperator(clock)
    parent=control.model_parent(clock)
    pilot=Controller(operator,campaign_id='d'*32,resource_sha256=control.MANIFEST,expires_epoch_us=1160000000,
        handoff_deadline_monotonic=200,peers=control.PEERS,boots=control.BOOTS,radio_permission=control.PERMISSION,
        parent=parent,clock=clock.mono,epoch=clock.epoch,sleep=clock.sleep,ip_startup_mode=READY_MODE)
    original_deadline=pilot.deadline;pilot.mixed_piece(control.REQUEST,control.RESPONSE)
    packages=[e[2] for e in operator.events if e[0]=='prepare']
    assert len(packages)==8 and pilot.deadline<=original_deadline
    for p in packages:
        if p['transport']=='tcp':
            assert p['grant']['schema']=='meshticulum.ip-service-run.v2' and p['grant']['startup_mode']==READY_MODE
            assert p['parent']['child']['cost']==dict(radio_attempts=0,estimated_airtime_us=0,ip_frames=2048,
                tx_stream_bytes=1048576,rx_stream_bytes=1048576)
            assert p['grant']['application_expires_epoch_us']==1160000000
        else:assert p['grant']['schema']=='meshticulum.service-run.v1' and 'startup_mode'not in p['grant']
    pilot.ip_startup_mode=None
    with pytest.raises(WireError):pilot.current()


@pytest.mark.parametrize('mode',[True,'another_mode',READY_MODE])
def test_pair_generator_refuses_unknown_mode_or_readiness_on_radio(mode):
    with pytest.raises(WireError):pair_packages(campaign_id='d'*32,resource_sha256=control.MANIFEST,
        application_expires_epoch_us=1160000000,sender='unit-c',receiver='unit-a',transport='lora',format='MR1.request',
        descriptor=dict(bytes=len(control.REQUEST),sha256=hashlib.sha256(control.REQUEST).hexdigest()),
        source=None,request=control.REQUEST,peers=control.PEERS,boots=control.BOOTS,now_epoch_us=1000000000,
        radio_permission=control.PERMISSION,ip_startup_mode=mode)


def test_readiness_cannot_be_added_after_original_parent_allocation(native_preparation):
    root,package,parent=native_preparation
    run=package['grant']['session']['session_id']
    original_child=canonical(package['parent']['child'])
    package['grant'].update(schema='meshticulum.ip-service-run.v2',startup_mode=READY_MODE)
    with pytest.raises(Exception):provision_original(root,package)
    assert not (root/'runs'/run).exists() and canonical(package['parent']['child'])==original_child
    db=sqlite3.connect(parent/'native.sqlite')
    try:
        assert db.execute('SELECT count(*) FROM prepared').fetchone()==(0,)
        assert db.execute('SELECT count(*) FROM starts').fetchone()==(0,)
    finally:db.close()


def test_v2_terminal_observation_is_tcp_and_exact_context_before_debt_collection(tmp_path,monkeypatch):
    _,_,p,_=ready_plan(tmp_path);context=ip_metadata_grant(p).digest.hex()
    parent=dict(parent_id='f'*32,run_id=RUN,started=True,grant_context_sha256=context)
    records={'grant.json':p,'provision.json':dict(transport='tcp',parent=parent),
        'result.json':dict(parent=parent)}
    class Reader:
        def read(self,path,maximum):return b'database'if path.name=='native.sqlite'else canonical(records[path.name])
    class Stop(Exception):pass
    captured=[]
    def observed(*args,**kwargs):captured.append(kwargs['legacy_scope']);raise Stop()
    monkeypatch.setattr(rpc,'boot',lambda:BOOT);monkeypatch.setattr(rpc,'clock',lambda:(200000000,400000000))
    monkeypatch.setattr(rpc,'collect',observed)
    monkeypatch.setattr(rpc,'profile_from_plan',lambda *a,**k:pytest.fail('v2 must not enter radio decoding'))
    with pytest.raises(Stop):rpc.terminal_observation(Reader(),'unit-a',SimpleNamespace(parent_id='f'*32,digest='a'*64),
        dict(runtime_sha256='a'*64,manifest_sha256='b'*64,phase_end_epoch_us=250000000),[RUN])
    assert captured[0].runs[0].transport=='tcp' and captured[0].runs[0].attempt_binding_sha256==context
    p['schema']='meshticulum.ip-service-run.v3'
    with pytest.raises(RuntimeError):rpc.terminal_observation(Reader(),'unit-a',SimpleNamespace(parent_id='f'*32),{},[RUN])
    assert len(captured)==1


@pytest.mark.parametrize('format',['MR1.request','MR1.response'])
def test_ready_public_node_two_processes_keep_one_shot_native_claims(tmp_path,format):
    request=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,256)
    payload=request if format=='MR1.request'else resource.BlockServer(lambda _:b'x'*256).handle(request)
    dirs,grant=node_fixture.prepare(tmp_path,payload,format,startup_mode=READY_MODE)
    receiver=node_fixture.start(dirs[1],'unit-b');sender=None
    try:
        node_fixture.ready(receiver,dirs[1]);sender=node_fixture.start(dirs[0],'unit-a')
        results=[]
        for process in (sender,receiver):
            out,error=process.communicate(timeout=10)
            assert process.returncode==0,(out,error);results.append(json.loads(out))
        assert all(r['complete'] and r['parent']['started'] and r['parent_caps']['starts']==1 for r in results)
        assert results[0]['byte_journal']['lanes']['READY']==1
        assert all(r['grant_context_sha256']==grant.digest.hex()for r in results)
        assert (dirs[1]/'receiver.bin').read_bytes()==payload
        assert all(not r.get('cleanup_errors')for r in results)
    finally:node_fixture.reap(sender);node_fixture.reap(receiver)


def test_ready_public_node_known_cut_keeps_ready_charge_and_original_claim(tmp_path):
    request=resource.encode_request('/packs/'+'a'*64+'/chunks/'+'b'*64+'.json',0,8192)
    payload=resource.BlockServer(lambda _:b'x'*8192).handle(request)
    dirs,grant=node_fixture.prepare(tmp_path,payload,'MR1.response',startup_mode=READY_MODE)
    receiver=node_fixture.start(dirs[1],'unit-b',cutoff=512);sender=None
    try:
        node_fixture.ready(receiver,dirs[1]);sender=node_fixture.start(dirs[0],'unit-a')
        results=[]
        for process in (sender,receiver):
            out,error=process.communicate(timeout=10)
            assert process.returncode!=0,(out,error);results.append(json.loads(out))
        assert all(not r['complete']and r['parent_caps']['starts']==1 for r in results)
        assert results[0]['byte_journal']['lanes']['READY']==1
        assert results[0]['byte_journal']['reserved_stream_bytes']>=83
        assert results[1]['test_tcp_loss']['known_rx_stream_bytes']==512 and results[1]['byte_journal']['fenced']
        assert not (dirs[1]/'receiver.bin').exists() and (dirs[1]/'started').exists()
    finally:node_fixture.reap(sender);node_fixture.reap(receiver)
