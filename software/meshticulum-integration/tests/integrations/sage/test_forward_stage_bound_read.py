"""Genuine provider, paid native Radio/PTY; SDK and physical facts modeled."""
from collections import Counter
import copy
import os
import threading
import time

import pytest

from lantern_meshticulum.sage.forward_operation import ForwardOperationError
from lantern_meshticulum.sage.forward_radio import ForwardRadioError,ForwardRadioFactory
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwnerError,CONTROL
from tests.integrations.sage.test_forward_radio_facts_action import (
    active, retained, revoke, PURPOSE_DOMAIN, ENROLL_DOMAIN,
)
from tests.integrations.sage.test_forward_radio_native_fence import supervisor_model


def bound(r):
    return r.runner._act(lambda:r.runner._bound())


def metadata(r):return r.owner._stage_read is not None


def test_one_bound_keeps_three_metadata_fences_two_snapshots_and_original_native(tmp_path,monkeypatch,record_property):
    with supervisor_model(tmp_path,monkeypatch),active(tmp_path,monkeypatch) as r:
        requests=[];stages=[]
        r.client._client.event_hooks['response'].append(lambda value:requests.append(value.request.url.path))
        r.source.hook=lambda:stages.append(r.owner._stage_read.phase) if metadata(r) else None
        # The original non-executing diagnostic remains the complete legacy path.
        r.runner._bound();legacy=Counter(requests);requests.clear();stages.clear();r.action['calls']=0
        bound(r);first=Counter(requests)
        assert stages==['operation','budget','budget'] and r.action['calls']==2
        assert first['/v1/workflows/'+r.adoption.record_id]==3
        for target in (r.provider._purpose,r.provider._enrollment):
            assert legacy['/v1/workflows/'+target.record_id]==5
            assert first['/v1/workflows/'+target.record_id]==2
        assert sum(first.values())<sum(legacy.values())
        bound(r)
        assert Counter(requests)==first+first and stages==['operation','budget','budget']*2
        assert r.owner._stage_read is None and r.op.budget._bound_read is None
        assert r.state.rows==r.saved_rows
        record_property('legacy_bound_requests',sum(legacy.values()))
        record_property('selected_bound_requests',sum(first.values()))
        retained(r)


@pytest.mark.parametrize('at',[1,2,3])
@pytest.mark.parametrize('change',['debt','epoch','expiry'])
def test_each_metadata_fence_refuses_changed_native_evidence(tmp_path,monkeypatch,at,change):
    with active(tmp_path,monkeypatch) as r:
        seen=[]
        def mutate():
            if not metadata(r):return
            seen.append(True)
            if len(seen)!=at:return
            if change=='debt':r.source.evidence='f'*64
            elif change=='epoch':r.state.instance_id='c'*64
            else:r.state.now=(r.state.now[0],r.owner.intention.application_deadline_monotonic_us)
        r.source.hook=mutate
        with pytest.raises((ForwardRadioError,ForwardOperationError,ForwardNativeOwnerError)):bound(r)
        assert len(seen)==at and r.action['calls']==0
        assert r.owner._stage_read is None and r.op.budget._bound_read is None
        assert r.source.close_calls==0 and not r.source.closed
        retained(r)


@pytest.mark.parametrize('target,domain',[('purpose',PURPOSE_DOMAIN),('enrollment',ENROLL_DOMAIN)])
def test_revoked_signed_key_is_refused_by_required_final_native_callback(tmp_path,monkeypatch,target,domain):
    with active(tmp_path,monkeypatch) as r:
        seen=[]
        def mutate():
            if not metadata(r):return
            seen.append(True)
            if len(seen)==3:revoke(r,getattr(r.provider,'_'+target),domain)
        r.source.hook=mutate
        with pytest.raises((ForwardRadioError,ForwardOperationError,ForwardNativeOwnerError)):bound(r)
        assert len(seen)==3 and r.action['calls']==0
        assert r.owner._stage_read is None and not r.stage._native_observing
        retained(r)


@pytest.mark.parametrize('change',['permit','body','revision','slot-type','cost-type','native-method','native-code','store','source','scope'])
def test_mid_read_paid_or_collaborator_drift_cannot_reach_native(tmp_path,monkeypatch,change):
    with active(tmp_path,monkeypatch) as r:
        reached=[]
        def mutate():
            if not metadata(r) or reached:return
            if change=='permit':r.op.budget._permits=dict(r.op.budget._permits)
            elif change=='body':r.op.budget._body=b'{}'
            elif change=='revision':r.runner.admission.revision+=1
            elif change=='slot-type':object.__setattr__(r.child,'slot',0.0)
            elif change=='cost-type':object.__setattr__(r.child.cost,'ip_frames',False)
            elif change=='native-method':r.runner._native=lambda *_:True
            elif change=='native-code':monkeypatch.setattr(ForwardRadioFactory._native,'__code__',(lambda *_:True).__code__)
            elif change=='store':r.op.budget.store=copy.copy(r.op.budget.store)
            elif change=='source':r.provider.accounting=copy.copy(r.source)
            else:
                grant=r.authority._grants[id(r.owner.native_context)]
                grant['operations']=grant['operations']-{CONTROL}
            reached.append(True)
        r.source.hook=mutate
        with pytest.raises((ForwardRadioError,ForwardOperationError,ForwardNativeOwnerError)):bound(r)
        assert reached==[True] and r.action['calls']==0
        assert r.owner._stage_read is None and r.op.budget._bound_read is None
        assert r.source.close_calls==0 and not r.runner.retired and r.op.position==0
        assert r.state.rows[r.op.budget.record_id]==r.saved_rows[r.op.budget.record_id]
        assert all(os.fstat(fd).st_ino==inode for fd,inode in r.saved_fds)


@pytest.mark.parametrize('action',['time','crypto','provider','budget','runner','cleanup','release','foreign-drain'])
def test_caught_reentry_or_cleanup_is_sticky_without_releasing_owned_resources(tmp_path,monkeypatch,action):
    with active(tmp_path,monkeypatch) as r:
        reached=[]
        def mutate():
            if not metadata(r) or reached:return
            reached.append(True)
            assert r.runner._executing and r.owner._busy and r.op._busy and r.op.budget._busy
            assert r.stage._native_observing and r.adoption._child.observing
            if action=='cleanup':assert r.stage._cleanup(time.monotonic()+1) is False
            elif action=='release':assert r.adoption.release() is False
            elif action=='foreign-drain':
                results=[]
                worker=threading.Thread(target=lambda:results.append(r.owner._registry.close(timeout=0)))
                worker.start();worker.join(2)
                assert not worker.is_alive() and results==[False]
            else:
                call={'time':r.owner.time,'crypto':r.op.session.authorize,
                    'provider':lambda:r.provider.operation,'budget':lambda:r.op.budget.check(r.runner.admission),
                    'runner':r.runner.start}[action]
                with pytest.raises(Exception):call()
            assert all(os.fstat(fd).st_ino==inode for fd,inode in r.saved_fds)
        r.source.hook=mutate
        with pytest.raises((ForwardRadioError,ForwardOperationError,ForwardNativeOwnerError)):bound(r)
        assert reached==[True] and r.action['calls']==0
        assert r.owner._stage_read is None and r.op.budget._bound_read is None
        assert r.source.close_calls==0 and r.op.position==0 and not r.runner.retired
        assert r.state.rows[r.op.budget.record_id]==r.saved_rows[r.op.budget.record_id]


def test_native_callback_still_checks_mutation_after_metadata_interval(tmp_path,monkeypatch):
    with active(tmp_path,monkeypatch) as r:
        reached=[]
        def mutate(_,at):
            assert r.owner._stage_read is None and r.op.budget._bound_read is None
            if at==2:r.source.evidence='f'*64;reached.append(True)
        r.action['mutate']=mutate
        with pytest.raises((ForwardRadioError,ForwardOperationError,ForwardNativeOwnerError)):bound(r)
        assert reached==[True] and r.action['calls']==2
        assert r.source.close_calls==0 and r.op.position==0 and not r.runner.retired
        retained(r)


@pytest.mark.parametrize('change',['forged-clock','clock-field','clock-delegate','read-delegate'])
def test_clock_sites_have_no_writable_or_repeatable_permission(tmp_path,monkeypatch,change):
    with active(tmp_path,monkeypatch) as r:
        reached=[]
        class EqualCallable:
            def __eq__(self,_):return True
            def __call__(self):pytest.fail('substitute clock executed')
        def mutate():
            if not metadata(r) or reached:return
            reached.append(True);token=r.owner._stage_read
            if change=='forged-clock':
                with pytest.raises(ForwardOperationError):r.op.check_bound_read(token,EqualCallable())
            elif change=='clock-field':
                token._clock_permit=object()
                with pytest.raises(ForwardOperationError):r.owner.time()
            elif change=='clock-delegate':token._clock_observer=lambda:r.state.now
            else:token._read_action=lambda:token
        r.source.hook=mutate
        with pytest.raises((ForwardRadioError,ForwardOperationError,ForwardNativeOwnerError)):bound(r)
        assert reached==[True] and r.action['calls']==0
        retained(r)


@pytest.mark.parametrize('when',['before','during'])
@pytest.mark.parametrize('change',['radio-helper','owner-default','owner-read','purpose','enrollment'])
def test_final_native_key_chain_code_and_defaults_cannot_be_substituted(tmp_path,monkeypatch,when,change):
    from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner
    from lantern_meshticulum.sage.noise_purpose import NoisePurpose
    from lantern_meshticulum.sage.noise_native_enrollment import NativeNoiseEnrollment
    with active(tmp_path,monkeypatch) as r:
        reached=[]
        def mutate():
            if reached or when=='during' and not metadata(r):return
            reached.append(True)
            if change=='owner-default':monkeypatch.setattr(ForwardNativeOwner._observe,'__kwdefaults__',dict(live=True,keys=False,storage=True))
            else:
                method={'radio-helper':ForwardRadioFactory._native_current,
                    'owner-read':ForwardNativeOwner._observe_radio,
                    'purpose':NoisePurpose.exchange_check,'enrollment':NativeNoiseEnrollment.exchange_check}[change]
                monkeypatch.setattr(method,'__code__',(lambda *args,**kwargs:True).__code__)
        if when=='before':mutate()
        else:r.source.hook=mutate
        with pytest.raises((ForwardRadioError,ForwardOperationError,ForwardNativeOwnerError)):bound(r)
        assert reached==[True] and r.action['calls']==0
        retained(r)


@pytest.mark.parametrize('target',['retired','adoption-closed'])
def test_false_radio_terminal_flag_cannot_replace_real_terminal_proof(tmp_path,monkeypatch,target):
    with active(tmp_path,monkeypatch) as r:
        if target=='retired':r.stage._retired=True
        else:r.adoption._closed=True
        with pytest.raises((ForwardRadioError,ForwardOperationError,ForwardNativeOwnerError)):bound(r)
        assert r.owner._stage_read is None and not r.runner.retired and r.op.position==0
        assert r.source.close_calls==0 and r.state.rows[r.op.budget.record_id]==r.saved_rows[r.op.budget.record_id]
        assert all(os.fstat(fd).st_ino==inode for fd,inode in r.saved_fds)


def test_selected_native_tcp_retirement_keeps_terminal_legacy_checks(tmp_path,record_property):
    from lantern_meshticulum.sage import forward_native_provider as module
    from lantern_meshticulum.sage.forward_tcp import ForwardTcpStage,plan_stage
    from tests.integrations.sage.native_canary_helpers import generated_canary_pair,CANARY_PRIOR_DEBT
    from tests.integrations.sage.test_forward_tcp import native_plan,grant_files,unused_address
    clock=lambda:(time.time_ns()//1000,time.monotonic_ns()//1000)
    stages=[];seen=[[],[]];paths=[Counter(),Counter()]
    with generated_canary_pair(module,clock=clock) as pair:
        packet=pair.operations[0].packet();addresses=unused_address(),unused_address()
        try:
            for i,owner in enumerate(pair.owners):
                owner.resource._forward_radio=owner.resource._coordinator=owner.resource._radio_observer=None
                profile=plan_stage(owner,native_plan(owner,packet,0,addresses),b's'*32,slot=0,kind=1)
                directory=tmp_path/str(i);grant_files(directory,profile)
                stage=ForwardTcpStage(owner,profile,directory,
                    native_admission=lambda p,owner=owner: p.child.host_boot_id==owner.intention.host_boot_id,
                    ready=(lambda p:stages[0].prepare(payload=packet) is not None) if i==1 else None)
                stages.append(stage)
                def counted(response,i=i,owner=owner):
                    paths[i][response.request.url.path]+=1
                    if owner._stage_read is not None:
                        assert stages[i]._stage_observation is owner._stage_read
                        seen[i].append(owner._stage_read.phase)
                pair.endpoints[i].client._client.event_hooks['response'].append(counted)
            # Receiver's original listener callback opens the genuine sender on
            # the same creator thread. No worker/native callback is replaced.
            stages[1].prepare()
            for stage in stages:
                assert stage.runner._stage_read_refs is not None
                stage.runner.start()
            end=time.monotonic()+35
            retired_reads=[]
            while not all(stage.runner.retired for stage in stages):
                for i,stage in enumerate(stages):
                    if stage.runner.retired:continue
                    stage.runner.step()
                    if stage.runner.wire_complete:
                        before=paths[i].copy()
                        assert pair.owners[i].retire_stage(stage.runner) is True
                        retired_reads.append((i,before))
                assert time.monotonic()<end
            for i,stage in enumerate(stages):
                before=next(counts for index,counts in retired_reads if index==i)
                assert stage._retired and stage.runner.retired and stage._terminal(stage.profile.child,stage.runner)
                assert stage._stage_observation is None and pair.owners[i]._stage_read is None
                assert pair.operations[i].position==1 and pair.operations[i].budget.prior==CANARY_PRIOR_DEBT
                assert paths[i]['/v1/workflows/'+pair.providers[i]._purpose.record_id]>before['/v1/workflows/'+pair.providers[i]._purpose.record_id]
                assert seen[i] and set(seen[i])=={'operation','budget'}
                assert all(value.fileno()==-1 for value,_ in stage._sockets)
                record_property(f'endpoint_{i}_selected_metadata_callbacks',len(seen[i]))
            assert pair.sources[0].actual_usage is pair.sources[1].actual_usage is None
        finally:
            for stage in stages:stage._cleanup(time.monotonic()+3)


@pytest.mark.parametrize('action',['cleanup','native-reentry','read-reentry','foreign-drain'])
def test_tcp_metadata_marker_retains_all_owned_handles_during_caught_cleanup(tmp_path,action):
    import socket
    from lantern_meshticulum.sage import forward_native_provider as module
    from lantern_meshticulum.sage.forward_tcp import ForwardTcpStage,ForwardTcpError,plan_stage
    from tests.integrations.sage.native_canary_helpers import generated_canary_pair,CANARY_PRIOR_DEBT
    from tests.integrations.sage.test_forward_tcp import native_plan,grant_files,unused_address
    clock=lambda:(time.time_ns()//1000,time.monotonic_ns()//1000)
    with generated_canary_pair(module,clock=clock) as pair,socket.socket() as listener:
        owner=pair.owners[0];op=pair.operations[0];p=pair.providers[0];source=pair.sources[0]
        state=pair.endpoints[0].state;packet=op.packet()
        owner.resource._forward_radio=owner.resource._coordinator=owner.resource._radio_observer=None
        listener.bind(('127.0.0.1',0));listener.listen(1)
        profile=plan_stage(owner,native_plan(owner,packet,0,(unused_address(),listener.getsockname())),b's'*32,slot=0,kind=1)
        directory=tmp_path/'tcp';grant_files(directory,profile)
        stage=ForwardTcpStage(owner,profile,directory,
            native_admission=lambda prof: prof is profile and state.owner_live)
        reached=[]
        try:
            runner=stage.prepare(payload=packet)
            paid=copy.deepcopy(state.rows[op.budget.record_id]);handles=tuple(stage._handles.items())
            descriptors=tuple((fd,os.fstat(fd).st_ino) for fd in
                (stage._directory_fd,stage.worker.sock.fileno(),*stage.authority.fds.values()))
            def mutate(response):
                if owner._stage_read is None or reached:return
                reached.append(True);token=owner._stage_read
                assert stage._stage_observation is token
                if action=='cleanup':assert stage._cleanup(time.monotonic()+1) is False
                elif action=='native-reentry':
                    with pytest.raises(ForwardTcpError):stage._native(owner.context,profile.child,stage.transfer)
                elif action=='read-reentry':
                    with pytest.raises(ForwardTcpError):
                        with stage._observe_stage_read(token):pytest.fail('nested read entered')
                else:
                    results=[]
                    worker=threading.Thread(target=lambda:results.append(owner._registry.close(timeout=0)))
                    worker.start();worker.join(2)
                    assert not worker.is_alive() and results==[False]
                assert tuple(stage._handles.items())==handles
                assert all(os.fstat(fd).st_ino==inode for fd,inode in descriptors)
                assert listener.fileno()>=0 and not source.closed
            pair.endpoints[0].client._client.event_hooks['response'].append(mutate)
            with pytest.raises((ForwardTcpError,ForwardOperationError,ForwardNativeOwnerError)):
                runner._act(lambda:runner._bound())
            assert reached==[True] and stage._stage_observation is None and owner._stage_read is None
            assert not runner.retired and op.position==0 and op.budget.prior==CANARY_PRIOR_DEBT
            assert state.rows[op.budget.record_id]==paid and source.actual_usage is None
            assert all(os.fstat(fd).st_ino==inode for fd,inode in descriptors)
        finally:
            stage._cleanup(time.monotonic()+3)
            assert listener.fileno()>=0


@pytest.mark.parametrize('delegate',['_bindings','_clock_observer','_clock_valid','_read_action'])
def test_instance_delegate_code_is_pinned_before_dispatch(tmp_path,monkeypatch,delegate):
    with active(tmp_path,monkeypatch) as r:
        reached=[]
        def mutate():
            if not metadata(r) or reached:return
            reached.append(True);fn=getattr(r.owner._stage_read,delegate)
            # Build an inert function with the same closure arity, without
            # invoking it or replacing any production callback object.
            import types
            count=len(fn.__closure__ or ())
            cells=','.join('x'+str(i) for i in range(count))
            code='def outer('+cells+'):\n def inert(*args,**kwargs):\n  return ('+cells+',)\n return inert\n'
            namespace={};exec(code,namespace)
            inert=namespace['outer'](*([None]*count))
            assert len(inert.__code__.co_freevars)==count
            monkeypatch.setattr(fn,'__code__',inert.__code__)
        r.source.hook=mutate
        with pytest.raises((ForwardRadioError,ForwardOperationError,ForwardNativeOwnerError)):bound(r)
        assert reached==[True] and r.action['calls']==0
        retained(r)


@pytest.mark.parametrize('during_native',[False,True])
def test_original_delegate_table_is_independently_held_across_callbacks(tmp_path,monkeypatch,during_native):
    with active(tmp_path,monkeypatch) as r:
        saved=[];reached=[]
        def mutate():
            token=r.owner._stage_read
            if token is not None and not saved:saved.append(token)
            if reached or during_native or token is None:return
            replace(token)
        def replace(token):
            reached.append(True);fn=token._bindings
            count=len(fn.__closure__);cells=','.join('x'+str(i) for i in range(count))
            namespace={};exec('def outer('+cells+'):\n def inert(*args,**kwargs):\n  return ('+cells+',)\n return inert\n',namespace)
            monkeypatch.setattr(fn,'__code__',namespace['outer'](*([None]*count)).__code__)
            token._delegate_pins=tuple((name,f,f.__code__,f.__defaults__,f.__kwdefaults__)
                for name,f,_,_,_ in token._delegate_pins)
        r.source.hook=mutate
        if during_native:r.action['mutate']=lambda _,at:replace(saved[0]) if at==2 else None
        with pytest.raises((ForwardRadioError,ForwardOperationError,ForwardNativeOwnerError)):bound(r)
        assert reached==[True] and r.action['calls']==(2 if during_native else 0)
        assert not r.runner.retired and r.op.position==0 and r.source.close_calls==0
        assert r.state.rows[r.op.budget.record_id]==r.saved_rows[r.op.budget.record_id]
        assert all(os.fstat(fd).st_ino==inode for fd,inode in r.saved_fds)


def test_terminal_legacy_callback_drift_cannot_replace_selected_native(tmp_path):
    import inspect
    import socket
    from lantern_meshticulum.sage import forward_native_provider as module
    from lantern_meshticulum.sage.forward_budget import ForwardBudget
    from lantern_meshticulum.sage.forward_tcp import ForwardTcpStage,ForwardTcpError,plan_stage
    from tests.integrations.sage.native_canary_helpers import generated_canary_pair
    from tests.integrations.sage.test_forward_tcp import native_plan,grant_files,unused_address
    clock=lambda:(time.time_ns()//1000,time.monotonic_ns()//1000)
    with generated_canary_pair(module,clock=clock) as pair,socket.socket() as listener:
        owner=pair.owners[0];op=pair.operations[0];packet=op.packet()
        owner.resource._forward_radio=owner.resource._coordinator=owner.resource._radio_observer=None
        listener.bind(('127.0.0.1',0));listener.listen(1)
        profile=plan_stage(owner,native_plan(owner,packet,0,(unused_address(),listener.getsockname())),b's'*32,slot=0,kind=1)
        directory=tmp_path/'tcp';grant_files(directory,profile)
        stage=ForwardTcpStage(owner,profile,directory,native_admission=lambda prof:prof is profile)
        reached=[];false_calls=[]
        try:
            runner=stage.prepare(payload=packet)
            paid=copy.deepcopy(pair.endpoints[0].state.rows[op.budget.record_id])
            assert stage._cleanup(time.monotonic()+3) is True
            def mutate(response):
                if reached:return
                frame=inspect.currentframe()
                try:
                    while frame is not None:
                        if frame.f_code is ForwardBudget._snapshot.__code__:break
                        frame=frame.f_back
                    if frame is None:return
                finally:del frame
                assert owner._stage_read is None and stage._retired
                reached.append(True)
                runner._native=lambda *args:false_calls.append(True) or True
            pair.endpoints[0].client._client.event_hooks['response'].append(mutate)
            with pytest.raises((ForwardTcpError,ForwardOperationError,ForwardNativeOwnerError)):
                runner._act(lambda:runner._bound(live=False))
            assert reached==[True] and false_calls==[] and not runner.retired and op.position==0
            assert pair.endpoints[0].state.rows[op.budget.record_id]==paid
        finally:stage._cleanup(time.monotonic()+3)
