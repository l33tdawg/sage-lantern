# SPDX-License-Identifier: Apache-2.0
"""Actual local ledger writer/parser controls; modeled facts, no native evidence."""
import base64
from copy import deepcopy
from dataclasses import asdict
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest
from meshticulum.contracts.identity import PeerID
from meshticulum.resources import resource
from lantern_meshticulum.bench import pilot_resume_rpc as rpc, pilot_parent_snapshot as snapshot
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps, NativeFacts
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy, UnitCaps, Cost, child_caps, canonical
from lantern_meshticulum.bench.resume_parent_guard import record_prepared

BOOT='11111111-1111-4111-8111-111111111111'


@pytest.fixture
def actual_rows(monkeypatch,request):
    base=Path(tempfile.mkdtemp(prefix='rollover-controls-',dir=Path(__file__).parent));base.chmod(0o700)
    state=base/'state';state.mkdir(mode=0o700)
    for name in ('parents','runs'): (state/name).mkdir(mode=0o700)
    complete=getattr(request,'param',None)=='complete'
    parent_id=rpc.COMPLETE_PAID if complete else rpc.LATEST_PAID
    row_count,start_count=(10,10) if complete else (9,8)
    target=state/'parents'/parent_id;target.mkdir(mode=0o700)
    (target/'prepared-identities').mkdir(mode=0o700)
    limit=Cost(ip_frames=32768,tx_stream_bytes=16777216,rx_stream_bytes=16777216)
    units=tuple(UnitCaps(u,BOOT,PeerID('local-rollover-controls',c*64),limit,Cost(),'a'*64)
                for u,c in [('unit-b','b'),('unit-c','c')])
    policy=ParentPolicy(parent_id,BOOT,90000000,200000000,40000000,150000000,
                        units,limit.plus(limit),'d'*64)
    caps=NativeCaps.enroll(target/'native.sqlite',policy,unit='unit-b',clock=lambda:(100000000,800000000),
        facts=lambda:NativeFacts('unit-b',BOOT,units[0].peer),approve=lambda _:True,prior_check=lambda _:True,
        history_binding_sha256='e'*64)
    request=resource.encode_request('/packs/'+'e'*64+'/chunks/'+'f'*64+'.json',0,16356)
    ids=[];pins={};ninth=None
    try:
        for index in range(row_count):
            pair=pair_packages(campaign_id=f'{index+1:032x}',resource_sha256='e'*64,
                application_expires_epoch_us=180000000,sender='unit-c',receiver='unit-b',transport='tcp',
                format='MR1.request',descriptor=dict(bytes=len(request),sha256=rpc.sha(request)),source=None,
                request=request,peers={u.unit:asdict(u.peer) for u in units},boots={u.unit:BOOT for u in units},
                now_epoch_us=100000000,endpoints={'unit-b':('127.0.0.1',35001),'unit-c':('127.0.0.1',35002)})
            package=deepcopy(pair['packages']['unit-b']);child=child_caps(package,policy).to_dict()
            package.update(schema='meshticulum.public-pilot-package.v2',parent=dict(
                schema='meshticulum.public-parent-allocation.v1',parent_id=policy.parent_id,
                policy_digest=policy.digest,allocation_id=f'{index+1:064x}',child=child))
            prep=caps.prepare_claim(package);plan=deepcopy(package['grant'])
            plan['application_deadline_monotonic_us']=prep.application_deadline_monotonic_us
            receipt=caps.finalize_provision(prep,plan);run=prep.run_id;ids.append(run)
            staging=state/'runs'/'.staging';staging.mkdir(mode=0o700)
            for name,raw in [('grant.json',canonical(plan)+b'\n'),('active',b'retired\n'),
                ('provision.json',canonical(dict(schema='meshticulum.provisioned-public-pilot.v2',
                     unit='unit-b',run_id=run,transport='tcp',parent=receipt)))]:
                rpc.write_new(staging,name,raw)
            fd=os.open(staging,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            staging.rename(state/'runs'/run)
            try: record=record_prepared(state,caps,prep,fd);pins[run]=rpc.sha(canonical(record))
            finally: os.close(fd)
            if index<start_count:
                caps.consume_start(run,plan,base64.b64decode(package['secret_base64']))
                assert caps.close()
                caps=NativeCaps.open(target/'native.sqlite',policy,unit='unit-b',clock=lambda:(100000000,800000000),
                    facts=lambda:NativeFacts('unit-b',BOOT,units[0].peer),approve=lambda _:True)
            else: ninth=dict(run_id=run,grant_sha256=rpc.sha(canonical(plan)+b'\n'),prepared_identity_sha256=pins[run])
        assert caps.close();native_raw=(target/'native.sqlite').read_bytes();native_sha=rpc.sha(native_raw)
        memory=sqlite3.connect(':memory:');image=bytearray(native_raw);image[18]=image[19]=1
        memory.deserialize(bytes(image));memory.execute('PRAGMA query_only=ON');memory.execute('PRAGMA trusted_schema=OFF')
        scope=SimpleNamespace(parent_id=policy.parent_id,policy_digest=policy.digest,unit='unit-b')
        class Reader:
            name='native.sqlite'
            def read(self,name,maximum):
                assert name=='native.sqlite.approval';return (target/name).read_bytes()
        snapshot._schema(memory,'native');parsed,detail,_=snapshot._native(memory,scope,Reader());memory.close()
        monkeypatch.setattr(rpc,'STATE',state);monkeypatch.setattr(rpc,'boot',lambda:BOOT)
        monkeypatch.setattr(rpc,'RETIRED_UNSTARTED',ids[-1])
        yield state,parsed,dict(detail=detail),[dict(run_id=r) for r in ids[:start_count]],([] if complete else [ninth]),pins,native_sha
        assert rpc.sha((target/'native.sqlite').read_bytes())==native_sha
    finally:
        caps.close();shutil.rmtree(base)


def test_actual_native_writer_flat_projection_and_unknown_retired_claim(actual_rows):
    state,policy,observed,started,unstarted,pins,_=actual_rows
    detail=observed['detail'];assert (detail['claim_count'],detail['prepared_count'],detail['start_count'])==(9,9,8)
    assert all('run_id' in c['child'] and 'metadata' not in c['child'] for c in detail['claims'])
    rpc.qualify_history_claims(policy,'unit-b',observed,started,unstarted)
    with rpc.Reader() as reader:
        assert len(rpc.latest_preparation_files(reader,'unit-b',policy,observed,pins))==9
        evidence=rpc.retired_unstarted(reader,'unit-b',policy,observed,unstarted[0],lambda:(201000000,901000000))
    assert evidence['actual_cost'] is None and evidence['full_prepaid_caps']['ip_frames']==2048
    assert evidence['no_refund'] is True and evidence['live_terminal_ownership_observed'] is False
    with pytest.raises(RuntimeError): rpc.qualify_terminal(observed,dict(history_binding_sha256='e'*64),pins)


@pytest.mark.parametrize('actual_rows',['complete'],indirect=True)
def test_actual_complete_parent_writer_and_all_ten_pinned_preparations(actual_rows):
    state,policy,observed,started,unstarted,pins,_=actual_rows
    detail=observed['detail'];assert (detail['claim_count'],detail['prepared_count'],detail['start_count'])==(10,10,10)
    rpc.qualify_history_claims(policy,'unit-b',observed,started,unstarted)
    with rpc.Reader() as reader:
        assert len(rpc.latest_preparation_files(reader,'unit-b',policy,observed,pins))==10
    assert detail['prior_plus_prepaid_caps']['ip_frames']==20480


@pytest.mark.parametrize('actual_rows',['complete'],indirect=True)
@pytest.mark.parametrize('attack',['missing_start','omitted_run','identity_change','missing_pin'])
def test_complete_parent_refuses_partial_or_changed_actual_evidence(actual_rows,attack):
    state,policy,observed,started,unstarted,pins,_=actual_rows;run=started[-1]['run_id']
    if attack=='missing_start':observed['detail']['claims'][-1]['started']=False
    elif attack=='omitted_run':started.pop()
    elif attack=='identity_change':(state/'parents'/policy.parent_id/'prepared-identities'/(run+'.json')).write_bytes(b'{}')
    else:pins.pop(run)
    with rpc.Reader() as reader,pytest.raises(RuntimeError):
        rpc.qualify_history_claims(policy,'unit-b',observed,started,unstarted)
        rpc.latest_preparation_files(reader,'unit-b',policy,observed,pins)


@pytest.mark.parametrize('attack',['missing_pin','fake_started','grant_change','missing_retirement','identity_change','job_present'])
def test_actual_writer_based_rollover_refuses_changed_evidence(actual_rows,attack):
    state,policy,observed,started,unstarted,pins,_=actual_rows;run=unstarted[0]['run_id']
    if attack=='missing_pin': pins.pop(run)
    elif attack=='fake_started': next(c for c in observed['detail']['claims'] if c['child']['run_id']==run)['started']=True
    elif attack=='grant_change': (state/'runs'/run/'grant.json').write_bytes(b'{}')
    elif attack=='missing_retirement': (state/'runs'/run/'active').write_bytes(b'active\n')
    elif attack=='identity_change': (state/'parents'/policy.parent_id/'prepared-identities'/(run+'.json')).write_bytes(b'{}')
    else: rpc.write_new(state/'runs'/run,'job.json',b'{}')
    with rpc.Reader() as reader,pytest.raises((RuntimeError,KeyError)):
        rpc.qualify_history_claims(policy,'unit-b',observed,started,unstarted)
        rpc.latest_preparation_files(reader,'unit-b',policy,observed,pins)
        rpc.retired_unstarted(reader,'unit-b',policy,observed,unstarted[0],lambda:(201000000,901000000))
