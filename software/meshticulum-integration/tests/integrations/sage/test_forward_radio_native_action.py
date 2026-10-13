"""Actual adoption/SDK/PTY ownership; facts and firmware are source models only."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
import base64
import copy
import os
import sys
import threading
import time

import pytest

from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner, SCOPES
from lantern_meshticulum.sage.forward_radio import ForwardRadioFactory, NativeRadioObserver, ForwardRadioError
from lantern_meshticulum.sage import radio_native_adoption as adoption_module
from lantern_meshticulum.sage.radio_native_adoption import NativeRadioAdoption, DOMAIN, SCOPE
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_forward_radio import factory_model


@contextmanager
def active(tmp_path, monkeypatch):
    box={'enabled':False, 'calls':0, 'mutate':None, 'result':True}
    def facts(observer):
        observer._bound()
        # Only the physical sysfs facts are modeled. Both held FDs are real.
        for name in ('fd','lock_fd'):os.fstat(getattr(observer.transport,name))
        if box['enabled']:
            box['calls']+=1
            if box['mutate'] is not None:box['mutate'](box['r'],box['calls'])
        observer._bound()
        return box['result']
    with factory_model(tmp_path,monkeypatch) as r:
        # Bind the modeled facts hook before constructing the objects under test.
        # The fixture's unused factory has never adopted/opened the transport.
        monkeypatch.setattr(NativeRadioObserver,'_device_facts',facts)
        r.observer=NativeRadioObserver(r.owner,r.profile,r.transport)
        r.stage=ForwardRadioFactory(r.owner,r.profile,r.directory,r.transport,observer=r.observer)
        runner=r.stage.prepare(payload=r.packet)
        box.update(r=r,enabled=True)
        r.action=box;r.runner=runner
        r.saved_payment=copy.deepcopy(r.state.rows[r.op.budget.record_id])
        r.saved_fds=tuple((getattr(r.transport,n),os.fstat(getattr(r.transport,n)).st_ino)
                          for n in ('fd','lock_fd'))
        try:yield r
        finally:
            box['enabled']=False
            if not r.stage.adoption._closed:r.stage.adoption.release()


def invoke(r):return r.stage._native(r.owner.context,r.profile.child,r.stage.transfer)


def retained(r):
    assert r.state.rows[r.op.budget.record_id]==r.saved_payment
    assert r.saved_payment['payload']['body']['allocations'][0]['cost']==asdict(r.profile.child.cost)
    assert r.profile.child.cost.radio_attempts==256
    assert r.profile.child.cost.estimated_airtime_us==60000000
    assert r.stage.journal.snapshot()['attempts']==0
    assert all(os.fstat(fd).st_ino==inode for fd,inode in r.saved_fds)
    assert not r.stage._native_observing and not r.stage.adoption._child.observing


def test_three_fresh_full_observations_and_two_fact_probes_each_call(tmp_path,monkeypatch):
    codes={NativeRadioAdoption.current.__code__:'current',NativeRadioAdoption._guard.__code__:'guard',
           NativeRadioAdoption._assets.__code__:'assets',ForwardNativeOwner.phase_check.__code__:'phase'}
    with active(tmp_path,monkeypatch) as r:
        count=Counter();old=sys.getprofile()
        def profile(frame,event,arg):
            if event=='call' and frame.f_code in codes:count[codes[frame.f_code]]+=1
        try:
            sys.setprofile(profile)
            assert invoke(r) is True
            assert invoke(r) is True
        finally:sys.setprofile(old)
        assert count==dict(current=6,guard=48,assets=12,phase=48)
        assert r.action['calls']==4
        retained(r)
        assert r.stage.report()['native_qualified'] is False


@pytest.mark.parametrize('probe',[1,2])
@pytest.mark.parametrize('drift',['scope','vault','clock','grant','signed-revocation','method','malformed'])
def test_each_fact_probe_retains_fresh_permission_and_original_binding(tmp_path,monkeypatch,probe,drift):
    with active(tmp_path,monkeypatch) as r:
        completed=[]
        def mutate(r,at):
            if at!=probe:return
            if drift=='scope':
                with r.authority._mutex:
                    row=r.authority._grants[id(r.native_context)]
                    row['operations']=row['operations']-{SCOPE}
            elif drift=='vault':r.state.instance_id='c'*64
            elif drift=='clock':r.state.now=(r.state.now[0],r.profile.child.application_deadline_monotonic_us)
            elif drift=='grant':(r.directory/'active').write_bytes(b'retired\n')
            elif drift=='signed-revocation':
                row=r.state.rows[r.stage.adoption.record_id]
                body=row['payload']['body'];row['revision']+=1;body['revision']=row['revision'];body['state']='revoked'
                row['payload']['signature']=base64.b64encode(r.hardware.identity._signing_key.sign(DOMAIN+_canonical(body)).signature).decode()
            elif drift=='method':r.stage.adoption.current=lambda:True
            else:r.action['result']=1
            completed.append(True)
        r.action['mutate']=mutate
        with pytest.raises(ForwardRadioError):invoke(r)
        assert completed==[True] and r.stage._unknown and r.stage.adoption._uncertain
        retained(r)
        # Restoring the facts result cannot turn the refused action into authority.
        r.action['mutate']=None;r.action['result']=True
        with pytest.raises(ForwardRadioError):invoke(r)


@pytest.mark.parametrize('action',['reenter','close','drain','foreign-drain','cleanup','contain','retire'])
def test_caught_cleanup_or_reentry_cannot_release_fds_inside_observation(tmp_path,monkeypatch,action):
    with active(tmp_path,monkeypatch) as r:
        results=[];errors=[];completed=[]
        def mutate(r,at):
            if at!=1:return
            child=r.stage.adoption._child
            assert child.observing and child.observe_thread==threading.get_ident()
            if action=='reenter':
                with pytest.raises(ForwardRadioError):invoke(r)
            elif action=='close':r.owner.close()
            elif action in ('drain','foreign-drain'):
                def drain():
                    try:results.append(r.owner._registry.close(timeout=0))
                    except BaseException as error:errors.append(error)
                if action=='drain':drain()
                else:
                    thread=threading.Thread(target=drain);thread.start();thread.join(2)
                    assert not thread.is_alive()
                assert results==[False] and not errors
            elif action=='cleanup':assert r.stage._cleanup(time.monotonic()+1) is False
            elif action=='contain':assert r.stage._contain(r.profile.child,r.runner,time.monotonic()+1) is False
            else:
                with pytest.raises(ForwardRadioError):r.stage._retire(r.stage.transfer)
            assert all(os.fstat(fd).st_ino==inode for fd,inode in r.saved_fds)
            assert child.observing and not r.stage.adoption._closed
            completed.append(True)
        r.action['mutate']=mutate
        with pytest.raises(ForwardRadioError):invoke(r)
        assert r.stage._unknown and completed==[True] and not errors
        retained(r)


def test_existing_observation_marker_is_not_cleared_by_refusal(tmp_path,monkeypatch):
    with active(tmp_path,monkeypatch) as r:
        child=r.stage.adoption._child;child.observing=True;child.observe_thread=-1
        try:
            with pytest.raises(ForwardRadioError):invoke(r)
            assert child.observing and child.observe_thread==-1
            assert not r.stage._native_observing and r.action['calls']==0
        finally:child.observing=False;child.observe_thread=None
        retained(r)


@pytest.mark.parametrize('probe',[1,2])
@pytest.mark.parametrize('drift',['worker','fd','source','pin-pair'])
def test_each_probe_refuses_worker_fd_source_and_pin_coreplacement(tmp_path,monkeypatch,probe,drift):
    with active(tmp_path,monkeypatch) as r:
        completed=[];saved_fd=None;foreign_inode=None
        def mutate(r,at):
            nonlocal saved_fd,foreign_inode
            if at!=probe:return
            if drift=='worker':r.stage.worker.fenced=True
            elif drift=='fd':
                saved_fd=os.dup(r.transport.fd)
                foreign=os.open(tmp_path/'foreign',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
                try:
                    foreign_inode=os.fstat(foreign).st_ino;os.dup2(foreign,r.transport.fd)
                finally:os.close(foreign)
            elif drift=='source':
                source=tmp_path/'changed-source.py';source.write_bytes(b'changed native source\n')
                monkeypatch.setattr(adoption_module,'__file__',str(source))
            else:
                substitute=tuple(list(r.stage._native_methods))
                r.stage._native_methods=r.stage._native_method_refs=substitute
            completed.append(True)
        r.action['mutate']=mutate
        try:
            with pytest.raises(ForwardRadioError):invoke(r)
            assert completed==[True] and r.stage._unknown and r.stage.adoption._uncertain
            if drift=='fd':assert os.fstat(r.transport.fd).st_ino==foreign_inode
        finally:
            if saved_fd is not None:
                os.dup2(saved_fd,r.transport.fd);os.close(saved_fd)
        retained(r)


def test_newer_signed_adoption_does_not_allow_rollback_on_next_probe(tmp_path,monkeypatch):
    with active(tmp_path,monkeypatch) as r:
        record_id=r.stage.adoption.record_id;old=copy.deepcopy(r.state.rows[record_id])
        row=r.state.rows[record_id];row['revision']=row['payload']['body']['revision']=2
        row['payload']['signature']=base64.b64encode(r.hardware.identity._signing_key.sign(
            DOMAIN+_canonical(row['payload']['body'])).signature).decode()
        assert r.stage.adoption.current() is True and r.stage.adoption._highest==2
        completed=[]
        def mutate(r,at):
            if at==2:r.state.rows[record_id]=old;completed.append(True)
        r.action['mutate']=mutate
        with pytest.raises(ForwardRadioError):invoke(r)
        assert completed==[True] and r.stage.adoption._highest==2
        retained(r)
