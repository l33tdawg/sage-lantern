"""Genuine provider/grants/signed rows/PTYs; native facts and SDK are models."""
import base64
from collections import Counter
from contextlib import contextmanager
import copy
from dataclasses import asdict, replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

import pytest

from meshticulum.contracts.identity import ServiceKind
from lantern_meshticulum.bench.profiles import profile_for
from lantern_meshticulum.sage.forward_budget import Cost
from lantern_meshticulum.sage.forward_radio import ForwardRadioFactory, NativeRadioObserver, ForwardRadioError
from lantern_meshticulum.sage.forward_native_provider import ForwardNativeProvider, SETUP_SCOPES
from lantern_meshticulum.sage.mesh_inbox import _canonical
from lantern_meshticulum.sage.noise_purpose import SIGNATURE_DOMAIN as PURPOSE_DOMAIN
from lantern_meshticulum.sage.noise_native_enrollment import SIGNATURE_DOMAIN as ENROLL_DOMAIN
from lantern_meshticulum.sage import radio_native_adoption as adoption_module
from lantern_meshticulum.sage.radio_native_adoption import SCOPE
from meshticulum.bench.serial_port import ExclusiveSerial
from tests.integrations.sage import test_forward_radio as radio_fixture
from tests.integrations.sage.test_forward_native_provider import fixture, DEBT


@contextmanager
def provider_model(tmp_path):
    with fixture(scopes=SETUP_SCOPES+(SCOPE,)) as (r,p,source,bind):
        bind(scopes=SETUP_SCOPES+(SCOPE,));op=p.start(r.packet);o=p.owner;h=r.hardware
        h._forward_radio=h._coordinator=h._radio_observer=None;h._initializing=False
        packet=op.packet();peer=lambda p:dict(chain_id=p.chain_id,agent_id=p.public_key.hex())
        plan=dict(schema='meshticulum.service-run.v1',unit='unit-a',host_boot_id=r.state.boot,
            local_peer=peer(o.local),initiator=peer(o.context.initiator),responder=peer(o.context.responder),
            session_id=uuid.uuid4().hex,generation=uuid.uuid4().hex,issued_epoch_us=o.context.issued_epoch_us,
            expires_epoch_us=o.context.expires_epoch_us,profile=asdict(profile_for('sf7-500')),
            object=dict(bytes=len(packet),sha256=hashlib.sha256(packet).hexdigest(),chunk_index=0,object_id=o.context.digest.hex()),
            sender_unit='unit-a',country='MY',antenna_confirmed=True,max_frames=64,
            application_deadline_monotonic_us=o.intention.application_deadline_monotonic_us-1000000,
            service=dict(kind=ServiceKind.MESSAGE.value,format='MPF2.record',application_expires_epoch_us=o.context.expires_epoch_us))
        directory=tmp_path/'grant';directory.mkdir(mode=0o700)
        for name,raw in (('grant.json',_canonical(plan)),('secret.bin',b's'*32),('active',b'active\n')):
            fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as out:out.write(raw)
        master,slave=os.openpty();lock=os.open(tmp_path/'lock',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
        fcntl.flock(slave,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        wire=ExclusiveSerial.__new__(ExclusiveSerial);wire.fd,wire.lock_fd=slave,lock
        original=Path.read_text
        def kernel(path,*args,**kwargs):
            if str(path)!='/proc/locks':return original(path,*args,**kwargs)
            rows=[]
            for index,fd in enumerate((slave,lock)):
                try:info=os.fstat(fd)
                except OSError:continue
                rows.append(f'{index}: FLOCK ADVISORY WRITE {os.getpid()} {os.major(info.st_dev):x}:{os.minor(info.st_dev):x}:{info.st_ino} 0 EOF')
            return '\n'.join(rows)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Path,'read_text',kernel)
            r.owner,r.op,r.packet,r.directory,r.transport,r.master=o,op,packet,directory,wire,master
            r.provider,r.source=p,source
            try:yield r
            finally:
                if h._forward_radio is not None:h._forward_radio.release()
                wire.close();os.close(master)


@contextmanager
def active(tmp_path,monkeypatch):
    box=dict(enabled=False,calls=0,mutate=None,result=True)
    monkeypatch.setattr(radio_fixture,'model',provider_model)
    with radio_fixture.factory_model(tmp_path,monkeypatch) as r:
        def facts(observer):
            observer._bound()
            for name in ('fd','lock_fd'):os.fstat(getattr(observer.transport,name))
            if box['enabled']:
                box['calls']+=1
                if box['mutate'] is not None:box['mutate'](r,box['calls'])
            observer._bound();return box['result']
        monkeypatch.setattr(NativeRadioObserver,'_device_facts',facts)
        r.observer=NativeRadioObserver(r.owner,r.profile,r.transport)
        r.stage=ForwardRadioFactory(r.owner,r.profile,r.directory,r.transport,observer=r.observer)
        r.runner=r.stage.prepare(payload=r.packet);r.action=box
        r.adoption=r.stage.adoption;r.child=r.profile.child
        r.saved_rows=copy.deepcopy(r.state.rows)
        r.saved_fds=tuple((getattr(r.transport,n),os.fstat(getattr(r.transport,n)).st_ino) for n in ('fd','lock_fd'))
        box['enabled']=True
        try:yield r
        finally:
            box['enabled']=False
            r.source.hook=None
            r.stage._cleanup(time.monotonic()+3)
            if not r.adoption._closed:r.adoption.release()


def invoke(r,mode='native'):
    if mode=='device':return r.observer.device()
    if mode=='live':return r.stage._live()
    return r.stage._native(r.owner.context,r.child,r.stage.transfer)


def retained(r):
    assert r.state.rows[r.op.budget.record_id]==r.saved_rows[r.op.budget.record_id]
    assert r.op.budget.prior==r.provider._retained_debt==DEBT
    assert json.loads(r.provider._accounting_binding)['actual_usage'] is None
    assert r.child.cost==Cost(256,60000000) and r.op.position==0 and not r.runner.retired
    assert r.stage.journal.snapshot()['attempts']==0
    assert r.adoption._read_run is r.owner._radio_read is r.owner._radio_facts is None
    assert not r.owner._busy and not r.stage._native_observing and not r.adoption._child.observing
    assert all(os.fstat(fd).st_ino==inode for fd,inode in r.saved_fds)


@pytest.mark.parametrize('mode,rows,probes', [('device',2,1),('live',2,1),('native',3,2)])
def test_each_action_keeps_all_reads_and_facts_with_two_fresh_key_bookends(tmp_path,monkeypatch,record_property,mode,rows,probes):
    with active(tmp_path,monkeypatch) as r:
        requests=[];metadata=[]
        r.client._client.event_hooks['response'].append(lambda response:requests.append(response.request.url.path))
        r.source.hook=lambda:metadata.append(True) if not r.provider._purpose._observing else None
        assert invoke(r,mode) is True
        first=Counter(requests);first_metadata=len(metadata)
        assert first['/v1/workflows/'+r.adoption.record_id]==rows
        assert first['/v1/workflows/'+r.provider._purpose.record_id]==2
        assert first['/v1/workflows/'+r.provider._enrollment.record_id]==2
        assert first_metadata==6*rows+2*(rows-1)
        assert invoke(r,mode) is True
        assert Counter(requests)==first+first and len(metadata)==2*first_metadata
        assert r.action['calls']==2*probes and r.state.rows==r.saved_rows
        record_property('source_modeled_requests_per_action',sum(first.values()))
        record_property('adoption_reads_per_action',rows)
        record_property('accounting_metadata_fences_per_action',first_metadata)
        retained(r)


@pytest.mark.parametrize('row,position',[(row,position) for row in range(3) for position in range(1,7)])
def test_each_of_six_sdk_positions_in_every_read_keeps_fresh_accounting(tmp_path,monkeypatch,row,position):
    with active(tmp_path,monkeypatch) as r:
        seen=[];before=(r.adoption._highest,r.adoption._record)
        target=8*row+position
        def changed():
            if not r.provider._purpose._observing:
                seen.append(True)
                if len(seen)==target:r.source.evidence='f'*64
        r.source.hook=changed
        with pytest.raises(ForwardRadioError):invoke(r)
        assert len(seen)==target and (r.adoption._highest,r.adoption._record)==before
        assert r.adoption._uncertain and r.owner._uncertain
        retained(r)


def revoke(r,target,domain):
    row=r.state.rows[target.record_id];body=row['payload']['body']
    row['revision']+=1;body['revision']=row['revision'];body['state']='revoked'
    row['payload']['signature']=base64.b64encode(r.hardware.identity._signing_key.sign(domain+_canonical(body)).signature).decode()


@pytest.mark.parametrize('probe,change',[(probe,change) for probe in (1,2) for change in
    ('scope','epoch','clock','boot','active','source','accounting','worker','method','malformed')])
def test_each_fact_probe_is_fenced_by_original_live_bindings(tmp_path,monkeypatch,probe,change):
    with active(tmp_path,monkeypatch) as r:
        reached=[]
        def changed(r,at):
            if at!=probe:return
            if change=='scope':
                with r.authority._mutex:
                    grant=r.authority._grants[id(r.owner.native_context)];grant['operations']=grant['operations']-{SCOPE}
            elif change=='epoch':r.state.instance_id='c'*64
            elif change=='clock':r.state.now=(r.state.now[0],r.child.application_deadline_monotonic_us)
            elif change=='boot':r.state.boot=str(uuid.uuid4())
            elif change=='active':(r.directory/'active').write_bytes(b'retired\n')
            elif change=='source':
                path=tmp_path/'changed-source.py';path.write_bytes(b'changed\n');monkeypatch.setattr(adoption_module,'__file__',str(path))
            elif change=='accounting':r.source.actual=Cost()
            elif change=='worker':r.stage.worker.fenced=True
            elif change=='method':monkeypatch.setattr(ForwardNativeProvider,'_radio_native_check',lambda *_:True)
            else:r.action['result']=1
            reached.append(True)
        r.action['mutate']=changed
        with pytest.raises(ForwardRadioError):invoke(r)
        assert reached==[True] and r.stage._unknown and r.adoption._uncertain
        retained(r)


@pytest.mark.parametrize('target,domain',[('purpose',PURPOSE_DOMAIN),('enrollment',ENROLL_DOMAIN)])
def test_key_revocation_after_last_adoption_get_refuses_final_return(tmp_path,monkeypatch,target,domain):
    with active(tmp_path,monkeypatch) as r:
        gets=[];before=(r.adoption._highest,r.adoption._record)
        def after(response):
            if response.request.url.path!='/v1/workflows/'+r.adoption.record_id:return
            gets.append(True)
            if len(gets)==3:revoke(r,getattr(r.provider,'_'+target),domain)
        r.client._client.event_hooks['response'].append(after)
        with pytest.raises(ForwardRadioError):invoke(r)
        assert len(gets)==3 and (r.adoption._highest,r.adoption._record)==before
        retained(r)


def test_new_revision_is_pending_and_cannot_regress_between_reads(tmp_path,monkeypatch):
    with active(tmp_path,monkeypatch) as r:
        record=r.state.rows[r.adoption.record_id];old=copy.deepcopy(record)
        record['revision']=record['payload']['body']['revision']=2
        record['payload']['signature']=base64.b64encode(r.hardware.identity._signing_key.sign(
            adoption_module.DOMAIN+_canonical(record['payload']['body'])).signature).decode()
        seen=[];before=(r.adoption._highest,r.adoption._record)
        def changed(r,at):
            assert (r.adoption._highest,r.adoption._record)==before
            seen.append(at)
            if at==2:r.state.rows[r.adoption.record_id]=old
        r.action['mutate']=changed
        with pytest.raises(ForwardRadioError):invoke(r)
        assert seen==[1,2] and (r.adoption._highest,r.adoption._record)==before
        retained(r)


def test_newest_revision_commits_only_after_final_key_bookend(tmp_path,monkeypatch):
    with active(tmp_path,monkeypatch) as r:
        before=(r.adoption._highest,r.adoption._record);seen=[]
        def changed(r,at):
            assert (r.adoption._highest,r.adoption._record)==before
            row=r.state.rows[r.adoption.record_id];body=row['payload']['body']
            row['revision']+=1;body['revision']=row['revision']
            row['payload']['signature']=base64.b64encode(r.hardware.identity._signing_key.sign(
                adoption_module.DOMAIN+_canonical(body)).signature).decode()
            seen.append(at)
        r.action['mutate']=changed
        assert invoke(r) is True and seen==[1,2]
        assert r.adoption._highest==3 and r.adoption._record==_canonical(r.state.rows[r.adoption.record_id]['payload']['body'])
        retained(r)


@pytest.mark.parametrize('probe',[1,2])
def test_fd_replacement_is_refused_without_closing_the_foreign_descriptor(tmp_path,monkeypatch,probe):
    with active(tmp_path,monkeypatch) as r:
        seen=[];saved=[];foreign=[]
        def changed(r,at):
            if at!=probe:return
            saved.append(os.dup(r.transport.fd))
            fd=os.open(tmp_path/'foreign',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
            try:foreign.append(os.fstat(fd).st_ino);os.dup2(fd,r.transport.fd)
            finally:os.close(fd)
            seen.append(True)
        r.action['mutate']=changed
        try:
            with pytest.raises(ForwardRadioError):invoke(r)
            assert seen==[True] and os.fstat(r.transport.fd).st_ino==foreign[0]
        finally:
            if saved:os.dup2(saved[0],r.transport.fd);os.close(saved[0])
        retained(r)


@pytest.mark.parametrize('change',['none','method','token','store','radio-owner','adopted','adoption-action'])
def test_consistent_callback_mode_or_token_substitution_is_not_admitted(tmp_path,monkeypatch,change):
    with active(tmp_path,monkeypatch) as r:
        seen=[]
        def changed(r,at):
            if at!=1:return
            if change=='none':r.stage._facts_action=r.stage._facts_action_ref=None
            elif change=='method':monkeypatch.setattr(type(r.owner),'_observe_radio',lambda *_:True)
            elif change=='token':r.owner._radio_facts=tuple(list(r.owner._radio_facts))
            elif change=='radio-owner':r.hardware._forward_radio=None
            elif change=='adopted':r.adoption._adopted=False
            elif change=='adoption-action':r.adoption._read_action=r.adoption._read_action_ref=lambda *_:True
            else:r.adoption.store.client=object()
            seen.append(True)
        r.action['mutate']=changed
        with pytest.raises(ForwardRadioError):invoke(r)
        assert seen==[True] and r.stage._unknown
        if change=='token':assert r.owner._radio_facts is not None and r.owner._uncertain
        else:retained(r)


@pytest.mark.parametrize('action',['native','device','read','crypto','dispatch','authorize','provider','close','drain','foreign-drain','cleanup','release'])
def test_caught_reentry_or_cleanup_cannot_release_resources_during_facts(tmp_path,monkeypatch,action):
    with active(tmp_path,monkeypatch) as r:
        reached=[]
        def changed(r,at):
            if at!=1:return
            assert r.owner._busy and r.adoption._busy and r.stage._native_observing
            assert r.adoption._child.observing and r.owner._radio_read is r.adoption._read_run
            if action=='read':assert r.adoption.current() is False
            elif action=='close':assert r.owner.close() is False
            elif action=='release':assert r.adoption.release() is False
            elif action=='cleanup':assert r.stage._cleanup(time.monotonic()+1) is False
            elif action in ('drain','foreign-drain'):
                result=[]
                def drain():result.append(r.owner._registry.close(timeout=0))
                if action=='drain':drain()
                else:
                    thread=threading.Thread(target=drain);thread.start();thread.join(2);assert not thread.is_alive()
                assert result==[False]
            else:
                call={'native':lambda:invoke(r),'device':r.observer.device,'authorize':r.stage._authorize,
                    'provider':r.provider.generate,'dispatch':lambda:r.owner.dispatch_check(r.owner.context,r.owner.intention),
                    'crypto':lambda:r.owner.crypto_action_check(r.owner.context,r.owner.intention,r.owner.remote,
                        r.owner.context.initiator_noise_public_key,r.owner.context.digest)}[action]
                with pytest.raises(Exception):call()
            assert all(os.fstat(fd).st_ino==inode for fd,inode in r.saved_fds)
            reached.append(True)
        r.action['mutate']=changed
        with pytest.raises(ForwardRadioError):invoke(r)
        assert reached==[True] and r.stage._unknown
        retained(r)
