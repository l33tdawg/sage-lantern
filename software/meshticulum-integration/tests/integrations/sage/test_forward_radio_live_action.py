"""Fresh radio callback brackets on actual SDK/owned PTY; no native RF proof."""
from collections import Counter
from contextlib import contextmanager
import base64
from dataclasses import replace
import os
import sys
import threading
import time

import pytest

from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.serial_protocol import Message
from lantern_meshticulum.sage.forward_radio import ForwardRadioFactory, ForwardRadioError
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner
from lantern_meshticulum.sage.radio_native_adoption import NativeRadioAdoption, DOMAIN, SCOPE
from lantern_meshticulum.sage import radio_native_adoption as adoption_module
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_forward_radio import factory_model
from tests.integrations.sage.test_forward_radio_native_action import active, retained


@contextmanager
def observations():
    codes={NativeRadioAdoption.current.__code__:'current',NativeRadioAdoption._guard.__code__:'guard',
        NativeRadioAdoption._assets.__code__:'assets',ForwardNativeOwner.phase_check.__code__:'phase',
        RunAuthority.live.__code__:'authority'}
    count=Counter();old=sys.getprofile()
    def profile(frame,event,arg):
        if event=='call' and frame.f_code in codes:count[codes[frame.f_code]]+=1
    try:
        sys.setprofile(profile);yield count
    finally:sys.setprofile(old)


def invoke(r,mode):return getattr(r.stage,mode)()


@pytest.mark.parametrize('mode,current,facts,authority',[
    ('_live',2,1,0),('_authorize',4,2,1),('_owned',2,1,0)])
def test_each_call_has_fresh_complete_observations(tmp_path,monkeypatch,mode,current,facts,authority):
    with active(tmp_path,monkeypatch) as r:
        with observations() as count:
            assert invoke(r,mode) is True
            assert invoke(r,mode) is True
        assert count==dict(current=2*current,guard=16*current,assets=4*current,
            phase=16*current,**({'authority':2*authority} if authority else {}))
        assert r.action['calls']==2*facts
        retained(r)
        assert r.stage.report()['native_qualified'] is False


def test_real_setup_partial_arm_and_separate_application_owner_probe(tmp_path,monkeypatch):
    phases=[];old=sys.getprofile()
    live=ForwardRadioFactory._live.__code__;authorize=ForwardRadioFactory._authorize.__code__
    def profile(frame,event,arg):
        if event!='call' or frame.f_code not in (live,authorize):return
        factory=frame.f_locals['self'];child=factory.adoption._child
        phases.append((frame.f_code is authorize,child.opening,factory.owner._held,
            factory.worker is None,factory.journal is None,factory.transfer is None))
    with factory_model(tmp_path,monkeypatch) as r:
        try:
            sys.setprofile(profile);runner=r.stage.prepare(payload=r.packet)
        finally:sys.setprofile(old)
        assert any(not auth and opening and held and no_worker and no_journal and no_transfer
            for auth,opening,held,no_worker,no_journal,no_transfer in phases)
        assert any(auth and opening and held and not no_worker and not no_journal and no_transfer
            for auth,opening,held,no_worker,no_journal,no_transfer in phases)
        assert runner is r.op.active and not r.stage.adoption._child.opening
        assert r.commands==[Message.HELLO,Message.STATUS,Message.DISARM,Message.ARM,Message.STATUS]
        assert r.stage.journal.snapshot()['attempts']==0
        with observations() as count:
            assert r.stage.authority.application_live() is True
            assert r.stage.authority.application_live() is True
        assert count==dict(current=4,guard=32,assets=8,phase=32,authority=2)
        assert not r.stage._native_observing and not r.stage.adoption._child.observing
        assert r.stage._cleanup(time.monotonic()+3) is True


def mutate(r,drift):
    if drift=='scope':
        with r.authority._mutex:
            row=r.authority._grants[id(r.native_context)]
            row['operations']=row['operations']-{SCOPE}
    elif drift=='vault':r.state.instance_id='c'*64
    elif drift=='clock':r.state.now=(r.state.now[0],r.profile.child.application_deadline_monotonic_us)
    elif drift=='active':(r.directory/'active').write_bytes(b'retired\n')
    elif drift=='signed-revocation':
        row=r.state.rows[r.stage.adoption.record_id];body=row['payload']['body']
        row['revision']+=1;body['revision']=row['revision'];body['state']='revoked'
        row['payload']['signature']=base64.b64encode(r.hardware.identity._signing_key.sign(
            DOMAIN+_canonical(body)).signature).decode()
    else:r.action['result']=1


@pytest.mark.parametrize('mode,probe',[('_live',1),('_authorize',1),('_authorize',2)])
@pytest.mark.parametrize('drift',['scope','vault','clock','active','signed-revocation','malformed'])
def test_both_sides_of_each_fact_callback_remain_live(tmp_path,monkeypatch,mode,probe,drift):
    with active(tmp_path,monkeypatch) as r:
        completed=[]
        def callback(r,at):
            if at==probe:mutate(r,drift);completed.append(True)
        r.action['mutate']=callback
        with pytest.raises(ForwardRadioError,match='^forward_radio_unavailable$'):invoke(r,mode)
        assert completed==[True] and r.stage._unknown and r.stage.adoption._uncertain
        retained(r)
        r.action['mutate']=None;r.action['result']=True
        with pytest.raises(ForwardRadioError):invoke(r,mode)


@pytest.mark.parametrize('drift',['scope','vault','clock','active','signed-revocation','malformed'])
def test_authority_callback_cannot_publish_after_drift(tmp_path,monkeypatch,drift):
    original=RunAuthority.live;box={'r':None,'done':[]}
    def live(authority):
        value=original(authority);r=box['r']
        if r is not None and authority is r.stage.authority:
            assert r.stage._native_observing and r.stage.adoption._child.observing
            mutate(r,drift);box['done'].append(True)
            if drift=='malformed':return 1
        return value
    monkeypatch.setattr(RunAuthority,'live',live)
    with active(tmp_path,monkeypatch) as r:
        box['r']=r
        with pytest.raises(ForwardRadioError,match='^forward_radio_unavailable$'):r.stage._authorize()
        assert box['done']==[True] and r.action['calls']==1
        assert r.stage._unknown and r.stage.adoption._uncertain
        retained(r)
        box['r']=None


@pytest.mark.parametrize('mode',['_live','_authorize'])
@pytest.mark.parametrize('drift',['live','clock','owner_probe','grant','deadline','deadlines','authorize','pins'])
def test_original_authority_and_callback_pins(tmp_path,monkeypatch,mode,drift):
    with active(tmp_path,monkeypatch) as r:
        completed=[]
        def callback(r,at):
            if at!=1:return
            a=r.stage.authority
            if drift=='live':a.live=lambda:True
            elif drift=='clock':a.clock=lambda:r.state.now
            elif drift=='owner_probe':a.owner_probe=lambda:True
            elif drift=='grant':a.grant=replace(a.grant)
            elif drift=='deadline':a.application_deadline_us+=1000000
            elif drift=='deadlines':object.__setattr__(a.deadlines,'authority_deadline_monotonic',
                a.deadlines.authority_deadline_monotonic+1)
            elif drift=='authorize':r.stage.worker.authorize=r.stage._authorize_callback=lambda:True
            else:
                replacement=tuple(list(r.stage._authority_binding))
                r.stage._authority_binding=r.stage._authority_binding_ref=replacement
            completed.append(True)
        r.action['mutate']=callback
        with pytest.raises(ForwardRadioError):invoke(r,mode)
        assert completed==[True] and r.stage._unknown and r.stage.adoption._uncertain
        retained(r)


@pytest.mark.parametrize('mode',['_live','_authorize'])
@pytest.mark.parametrize('action',['reenter','native-reenter','close','cleanup','retire','foreign-drain'])
def test_caught_reentry_and_cleanup_keep_owned_fds(tmp_path,monkeypatch,mode,action):
    with active(tmp_path,monkeypatch) as r:
        completed=[];result=[];errors=[]
        def callback(r,at):
            if at!=1:return
            child=r.stage.adoption._child
            assert child.observing and child.observe_thread==threading.get_ident()
            if action=='reenter':
                with pytest.raises(ForwardRadioError):invoke(r,mode)
            elif action=='native-reenter':
                with pytest.raises(ForwardRadioError):r.stage._native(r.owner.context,r.profile.child,r.stage.transfer)
            elif action=='close':r.owner.close()
            elif action=='cleanup':assert r.stage._cleanup(time.monotonic()+1) is False
            elif action=='retire':
                with pytest.raises(ForwardRadioError):r.stage._retire(r.stage.transfer)
            else:
                def drain():
                    try:result.append(r.owner._registry.close(timeout=0))
                    except BaseException as error:errors.append(type(error).__name__)
                thread=threading.Thread(target=drain);thread.start();thread.join(2)
                assert not thread.is_alive() and result==[False] and not errors
            assert all(os.fstat(fd).st_ino==inode for fd,inode in r.saved_fds)
            assert child.observing and not r.stage.adoption._closed
            completed.append(True)
        r.action['mutate']=callback
        with pytest.raises(ForwardRadioError):invoke(r,mode)
        assert completed==[True] and r.stage._unknown and r.stage.adoption._uncertain
        retained(r)


def test_foreign_existing_marker_is_never_released(tmp_path,monkeypatch):
    with active(tmp_path,monkeypatch) as r:
        child=r.stage.adoption._child;child.observing=True;child.observe_thread=-1
        try:
            with pytest.raises(ForwardRadioError):r.stage._live()
            assert child.observing and child.observe_thread==-1
            assert not r.stage._native_observing and r.action['calls']==0
        finally:child.observing=False;child.observe_thread=None
        retained(r)


def test_interruption_releases_only_observation_marker(tmp_path,monkeypatch):
    with active(tmp_path,monkeypatch) as r:
        completed=[]
        def interrupt(r,at):
            completed.append(True);raise KeyboardInterrupt()
        r.action['mutate']=interrupt
        with pytest.raises(KeyboardInterrupt):r.stage._authorize()
        assert completed==[True] and r.stage._unknown and r.stage.adoption._uncertain
        retained(r)


@pytest.mark.parametrize('mode',['_live','_authorize'])
@pytest.mark.parametrize('drift',['scope','clock'])
def test_initial_exact_false_never_grants_authority_but_owned_containment_remains(tmp_path,monkeypatch,mode,drift):
    with active(tmp_path,monkeypatch) as r:
        now=r.state.now;grants=r.authority._grants[id(r.native_context)]['operations']
        mutate(r,drift)
        with pytest.raises(ForwardRadioError):invoke(r,mode)
        assert r.action['calls']==0 and r.stage.adoption._uncertain and not r.stage._unknown
        retained(r)
        r.state.now=now;r.authority._grants[id(r.native_context)]['operations']=grants
        with pytest.raises(ForwardRadioError):invoke(r,mode)
        assert r.action['calls']==0 and r.stage.adoption._uncertain and not r.stage._unknown
        retained(r)
        assert r.stage._cleanup(time.monotonic()+3) is True and r.observer.physical_disarmed is True
        assert r.stage.adoption._closed and r.stage.authority.closed
        assert r.state.rows[r.op.budget.record_id]==r.saved_payment
        assert not r.stage.report()['native_qualified']


@pytest.mark.parametrize('drift',['authority-pins','method','source','fd','reentry'])
def test_initial_false_cannot_hide_structural_drift(tmp_path,monkeypatch,drift):
    with active(tmp_path,monkeypatch) as r:
        original=r.hardware.configured;completed=[];saved_fd=None
        def configured():
            nonlocal saved_fd
            original()
            if drift=='authority-pins':
                substitute=tuple(list(r.stage._authority_binding))
                r.stage._authority_binding=r.stage._authority_binding_ref=substitute
            elif drift=='method':r.observer._device_facts=lambda:True
            elif drift=='source':
                source=tmp_path/'changed-source.py';source.write_bytes(b'changed source\n')
                monkeypatch.setattr(adoption_module,'__file__',str(source))
            elif drift=='fd':
                saved_fd=os.dup(r.transport.fd)
                foreign=os.open(tmp_path/'foreign',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
                try:os.dup2(foreign,r.transport.fd)
                finally:os.close(foreign)
            else:
                with pytest.raises(ForwardRadioError):r.stage._live()
            completed.append(True);return False
        r.hardware.configured=configured
        try:
            with pytest.raises(ForwardRadioError):r.stage._authorize()
            assert completed==[True] and r.action['calls']==0
            assert r.stage._unknown and r.stage.adoption._uncertain
        finally:
            if saved_fd is not None:os.dup2(saved_fd,r.transport.fd);os.close(saved_fd)
        retained(r)


@pytest.mark.parametrize('value',[1,None,False])
def test_initial_malformed_current_is_not_a_known_false(tmp_path,monkeypatch,value):
    original=NativeRadioAdoption.current;box={'enabled':False,'completed':[]}
    def current(adoption):
        result=original(adoption)
        if box['enabled']:
            assert result is True;box['completed'].append(True);return value
        return result
    monkeypatch.setattr(NativeRadioAdoption,'current',current)
    with active(tmp_path,monkeypatch) as r:
        box['enabled']=True
        with pytest.raises(ForwardRadioError):r.stage._authorize()
        assert box['completed']==[True] and r.action['calls']==0
        assert r.stage._unknown and r.stage.adoption._uncertain
        retained(r)
        box['enabled']=False
        with pytest.raises(ForwardRadioError):r.stage._authorize()
        assert box['completed']==[True] and r.action['calls']==0
        retained(r)
