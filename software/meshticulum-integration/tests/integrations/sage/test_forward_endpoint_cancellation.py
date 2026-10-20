"""Local cancellation only: real ownership/crypto, modeled RF and SDK IO."""
from contextlib import contextmanager
from dataclasses import asdict
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import traceback
import uuid

import pytest

from lantern_meshticulum.sage.forward_children import (ForwardCancellationPending,ForwardClosePermit,
    ForwardChildrenError)
from lantern_meshticulum.sage.forward_operation import ForwardOperationError
from lantern_meshticulum.sage.forward_operation import describe_child
from meshticulum.bench.services import ServiceTransfer
from tests.integrations.sage.test_forward_children import hardware_close_ready
from tests.integrations.sage.test_forward_native_provider import fixture,ModeledAccounting
from lantern_meshticulum.sage.forward_native_provider import SETUP_SCOPES,ForwardNativeProviderError
from lantern_meshticulum.sage.radio_native_adoption import SCOPE
from lantern_meshticulum.sage.mesh_inbox import _canonical
from lantern_meshticulum.sage.noise_purpose import NoisePurpose
from tests.integrations.sage.test_forward_radio_gfsk250 import factory250
from tests.test_meshticulum_private_carrier import carrier_pair


@contextmanager
def provider_radio_model(tmp_path):
    """Only kernel/device facts and firmware are modeled, as factory250 does."""
    from meshticulum.bench.serial_port import ExclusiveSerial
    from lantern_meshticulum.bench.profiles import profile_for
    with fixture(scopes=SETUP_SCOPES+(SCOPE,)) as (r,p,source,bind):
        context,_=bind(scopes=SETUP_SCOPES+(SCOPE,));op=p.start(r.packet);owner=p._owner;packet=op.packet()
        r.hardware._forward_radio=r.hardware._coordinator=r.hardware._radio_observer=None
        r.hardware._initializing=False
        peer=lambda p:dict(chain_id=p.chain_id,agent_id=p.public_key.hex())
        plan=dict(schema='meshticulum.service-run.v1',unit='unit-a',host_boot_id=r.state.boot,
            local_peer=peer(owner.local),initiator=peer(context.initiator),responder=peer(context.responder),
            session_id=uuid.uuid4().hex,generation=uuid.uuid4().hex,issued_epoch_us=context.issued_epoch_us,
            expires_epoch_us=context.expires_epoch_us,profile=asdict(profile_for('sf7-500')),
            object=dict(bytes=len(packet),sha256=hashlib.sha256(packet).hexdigest(),chunk_index=0,object_id=context.digest.hex()),
            sender_unit='unit-a',country='MY',antenna_confirmed=True,max_frames=64,
            application_deadline_monotonic_us=r.intention.application_deadline_monotonic_us-1000000,
            service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=context.expires_epoch_us))
        directory=tmp_path/'grant';directory.mkdir(mode=0o700)
        for name,raw in (('grant.json',_canonical(plan)),('secret.bin',b's'*32),('active',b'active\n')):
            fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as out:out.write(raw)
        master,slave=os.openpty();lock=os.open(tmp_path/'lock',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
        fcntl.flock(slave,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        transport=ExclusiveSerial.__new__(ExclusiveSerial);transport.fd,transport.lock_fd=slave,lock
        r.owner,r.op,r.packet,r.directory,r.transport,r.master=owner,op,packet,directory,transport,master
        r.provider,r.source=p,source
        original=Path.read_text
        def kernel(path,*args,**kwargs):
            if str(path)!='/proc/locks':return original(path,*args,**kwargs)
            rows=[]
            for i,fd in enumerate((slave,lock)):
                try:s=os.fstat(fd)
                except OSError:continue
                rows.append(f'{i}: FLOCK ADVISORY WRITE {os.getpid()} {os.major(s.st_dev):x}:{os.minor(s.st_dev):x}:{s.st_ino} 0 EOF')
            return '\n'.join(rows)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Path,'read_text',kernel)
            try:yield r
            finally:
                if r.hardware._forward_radio is not None:r.hardware._forward_radio.release()
                transport.close();os.close(master)


@pytest.mark.parametrize('started',[False,True])
def test_provider_actual_factory_cancel_closes_keys_and_accounting_last(tmp_path,monkeypatch,started):
    from tests.integrations.sage import test_forward_radio_gfsk250 as model
    monkeypatch.setattr(model,'model',provider_radio_model)
    with factory250(tmp_path,monkeypatch) as r:
        hardware_close_ready(r.hardware)
        try:runner=r.stage.prepare(payload=r.packet)
        except Exception as error:
            locations=[];seen=set();current=error
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                locations.append((type(current).__name__,[(Path(f.filename).name,f.name,f.lineno)
                    for f in traceback.extract_tb(current.__traceback__)]))
                current=current.__context__
            error.add_note('Sanitized original prepare exception locations: '+json.dumps(locations))
            raise
        if started:runner.start()
        rows=copy.deepcopy(r.state.rows);observations=r.source.observations
        assert r.provider.close() is False and not r.source.closed
        assert type(r.provider._purpose._abort_pending) is ForwardCancellationPending
        assert not r.provider._purpose._close_failed
        assert r.hardware.close(5) is True
        assert r.provider.close() is True and r.source.closed and r.source.close_calls==1
        assert r.source.observations==observations and r.state.rows==rows
        assert r.op._cancelled is runner and r.op.active is None and r.op.position==0 and not runner.retired
        assert r.firmware['frames']==[] and r.transport.fd is r.transport.lock_fd is None
        assert r.stage.report()['physical_disarmed'] is True


@pytest.mark.parametrize('started',[False,True])
def test_actual_factory_creator_cancellation_joins_without_success_or_refund(tmp_path,monkeypatch,started):
    with factory250(tmp_path,monkeypatch) as r:
        hardware_close_ready(r.hardware)
        runner=r.stage.prepare(payload=r.packet)
        if started:runner.start()
        rows=copy.deepcopy(r.state.rows);position=r.op.position
        assert r.stage.journal.snapshot()['attempts']==0
        assert r.hardware.close(5) is True
        assert r.op.active is None and r.op._cancelled is runner and r.op.position==position==0
        assert not runner.retired and not runner.wire_complete and runner.payload is None
        assert runner.transfer.cancelled and r.op.cached is None
        assert r.state.rows==rows and r.firmware['frames']==[]
        assert r.stage.report()['physical_disarmed'] is True
        assert r.transport.fd is r.transport.lock_fd is None
        assert r.hardware.close(1) is True and r.op._cancelled is runner


@contextmanager
def active_provider(tmp_path,*,started=False,contain=None,terminal=None,source_change=None):
    with fixture(source_change=source_change) as (r,p,source,bind):
        bind();op=p.start(r.packet);owner=p._owner;packet=op.packet()
        hardware_close_ready(r.hardware)
        class Clock:
            def __call__(self):return r.state.now
            def advance(self,us):r.state.now=tuple(v+us for v in r.state.now)
        service=dict(kind='protected_message',format='MPF2.record',
                     application_expires_epoch_us=owner.context.expires_epoch_us)
        with carrier_pair(tmp_path/'carrier',packet,Clock(),'unit-a','unit-b',91,service=service,
                          original_expires_epoch_us=owner.context.expires_epoch_us) as pair:
            transfer=ServiceTransfer(pair.sender);plan=describe_child(transfer,slot=0,kind=1)
            events=[]
            def contained(*args):
                events.append('contain');transfer.cancel()
                return True if contain is None else contain(*args)
            def stopped(*args):
                events.append('terminal')
                return transfer.cancelled if terminal is None else terminal(*args)
            runner=owner.prepare_stage(plan,open_child=lambda _:transfer,native_check=lambda *a:True,
                retire_child=lambda _:True,contain_child=contained,terminal_check=stopped,payload=packet)
            if started:runner.start()
            yield r,p,source,op,runner,events


def test_provider_pending_abort_completes_only_after_containment(tmp_path):
    with active_provider(tmp_path) as (r,p,source,op,runner,events):
        rows=copy.deepcopy(r.state.rows)
        assert p.close() is False
        pending=p._purpose._abort_pending
        assert type(pending) is ForwardCancellationPending
        assert not p._purpose._close_failed and not p._purpose._uncertain and not source.closed
        assert op.active is runner and not events
        assert r.hardware.close(2) is True
        assert events==['contain','terminal'] and p._purpose._abort_pending is None
        assert p._purpose._key is None and op.session._s is None
        assert source.closed and source.close_calls==1 and p._enrollment._closed
        assert op.active is None and op._cancelled is runner and not runner.retired and op.position==0
        assert r.state.rows==rows and p.close() is True
        assert r.hardware.close(1) is True and source.close_calls==1


@pytest.mark.parametrize('failure',['contain','terminal','unknown','budget','purpose','enrollment'])
def test_known_failures_remain_sticky_and_never_release_accounting(tmp_path,failure):
    with active_provider(tmp_path,contain=(lambda *a:False) if failure=='contain' else None,
                         terminal=(lambda *a:False) if failure=='terminal' else None) as (r,p,source,op,runner,events):
        before=copy.deepcopy(r.state.rows)
        assert p.close() is False
        if failure=='unknown':p._owner._registry._children[0].unknown=True
        elif failure=='budget':op.budget._uncertain=True
        elif failure=='purpose':p._purpose._close_failed=True
        elif failure=='enrollment':p._enrollment._uncertain=True
        assert r.hardware.close(2) is False and not source.closed
        assert r.state.rows==before and op.position==0 and not runner.retired
        assert not r.hardware._closed
        if failure=='purpose':assert p._purpose._close_failed
        if failure=='enrollment':assert p._enrollment._uncertain


@pytest.mark.parametrize('substitution',['pending','allocation','abort','source_method','optional_method'])
def test_exact_cleanup_bindings_refuse_substitution(tmp_path,substitution,monkeypatch):
    with active_provider(tmp_path) as (r,p,source,op,runner,events):
        assert p.close() is False
        registry=p._owner._registry
        if substitution=='pending':
            p._purpose._abort_pending=ForwardCancellationPending(registry,p._owner,op,runner)
        elif substitution=='allocation':
            op.budget._permits[id(runner.admission)]=object()
        elif substitution=='abort':p._purpose._abort=lambda:True
        elif substitution=='source_method':p._source_cleanup=(source,lambda source:True)
        else:monkeypatch.setattr(ModeledAccounting,'close_from_registry',lambda *a:True,raising=False)
        try:result=r.hardware.close(2)
        except ForwardChildrenError:result=False
        assert result is False and not source.closed and not r.hardware._closed
        assert op.position==0 and not runner.retired and registry._uncertain


@pytest.mark.parametrize('busy',['purpose','enrollment','source','runner'])
def test_idle_barrier_spends_no_permit_until_producer_unwinds(tmp_path,busy):
    with active_provider(tmp_path) as (r,p,source,op,runner,events):
        obj,name,value={'purpose':(p._purpose,'_observing',True),
            'enrollment':(p._enrollment,'_busy',True),'source':(source,'_busy',1),
            'runner':(runner,'_executing',True)}[busy]
        prior=getattr(obj,name,False);setattr(obj,name,value)
        try:
            assert r.hardware.close(.01) is False and not events and not source.closed
            assert not p._owner._registry._component_permits
        finally:setattr(obj,name,prior)
        assert r.hardware.close(2) is True and source.closed


def test_no_live_observation_or_new_permission_is_needed_after_expiry(tmp_path):
    with active_provider(tmp_path) as (r,p,source,op,runner,events):
        rows=copy.deepcopy(r.state.rows);observations=source.observations
        r.state.now=(r.intention.application_expires_epoch_us,r.intention.application_deadline_monotonic_us)
        r.state.owner_live=False
        assert p.close() is False and r.hardware.close(2) is True
        assert source.observations==observations and r.state.rows==rows and not runner.retired


def test_forged_operation_permit_never_acknowledges_cancellation(tmp_path):
    with active_provider(tmp_path) as (r,p,source,op,runner,events):
        registry=p._owner._registry;op.close()
        forged=ForwardClosePermit(registry,p._owner,op,object(),time.monotonic()+1)
        with pytest.raises(ForwardChildrenError):op.acknowledge_cancelled(forged,runner)
        assert op.active is runner and not forged._used and not source.closed
        assert r.hardware.close(2) is True
        with pytest.raises(ForwardOperationError):op.acknowledge_cancelled(forged,runner)


@pytest.mark.parametrize('entry',['registry','provider'])
def test_caught_cleanup_reentry_cannot_issue_later_key_or_source_permits(tmp_path,monkeypatch,entry):
    original=NoisePurpose.close_from_registry;holder={};called=[]
    def close(purpose,permit):
        result=original(purpose,permit)
        if 'values' in holder:
            r,p,source,op,runner,events=holder['values']
            assert purpose is p._purpose and op.active is None and not source.closed
            called.append(True)
            if entry=='registry':assert p._owner._registry.close(.01) is False
            else:
                with pytest.raises(ForwardNativeProviderError):p._registry_finish_close(permit)
            assert not source.closed and not p._enrollment._closed
        return result
    monkeypatch.setattr(NoisePurpose,'close_from_registry',close)
    with active_provider(tmp_path) as values:
        holder['values']=values;r,p,source,op,runner,events=values
        assert p.close() is False
        rows=copy.deepcopy(r.state.rows)
        assert r.hardware.close(2) is False and called==[True]
        assert p._uncertain and p._owner._registry._uncertain
        assert not source.closed and source.close_calls==0 and not p._enrollment._closed
        assert op.active is None and op._cancelled is runner and not runner.retired
        assert op.position==0 and r.state.rows==rows and p.close() is False


@pytest.mark.parametrize('field',['allocation','body','position'])
def test_cancelled_paid_evidence_is_rechecked_after_acknowledgement(tmp_path,monkeypatch,field):
    original=NoisePurpose.close_from_registry;holder={};called=[]
    def close(purpose,permit):
        result=original(purpose,permit)
        if 'values' in holder:
            r,p,source,op,runner,events=holder['values']
            assert op.active is None and op._cancelled is runner and not source.closed
            called.append(True)
            if field=='allocation':op.budget._permits[id(runner.admission)]=object()
            elif field=='body':op.budget._body=b'changed protected body'
            else:op.position+=1
        return result
    monkeypatch.setattr(NoisePurpose,'close_from_registry',close)
    with active_provider(tmp_path) as values:
        holder['values']=values;r,p,source,op,runner,events=values
        assert p.close() is False
        rows=copy.deepcopy(r.state.rows)
        assert r.hardware.close(2) is False and called==[True]
        assert not source.closed and not p._enrollment._closed and p._uncertain
        assert r.state.rows==rows and not runner.retired


class ReentrantAccounting(ModeledAccounting):
    def close(self):
        self.events.append('source entered')
        p=self.provider
        assert p._purpose._abort_pending is None and p._purpose._key is None and p._enrollment._closed
        assert not self.closed and p._owner._registry.close(.01) is False
        self.events.append('nested drain unwound')
        assert not self.closed
        result=super().close()
        self.events.append('source released')
        return result


def test_source_callback_reentry_stays_failed_and_releases_only_after_unwind(tmp_path):
    def source_change(source):source.__class__=ReentrantAccounting;source.events=[]
    with active_provider(tmp_path,source_change=source_change) as (r,p,source,op,runner,events):
        assert p.close() is False
        rows=copy.deepcopy(r.state.rows)
        assert r.hardware.close(2) is False
        assert source.events==['source entered','nested drain unwound','source released']
        assert source.closed and source.close_calls==1 and p._uncertain and p._owner._registry._uncertain
        assert p.close() is False and r.hardware.close(1) is False and source.close_calls==1
        assert op.active is None and op.position==0 and not runner.retired and r.state.rows==rows


@pytest.mark.parametrize('field',['pending','abort'])
def test_purpose_never_clears_a_token_or_abort_substituted_during_fencing(tmp_path,monkeypatch,field):
    original=NoisePurpose._fence;holder={};called=[];substitute=object()
    def fence(purpose,*,abort=True):
        result=original(purpose,abort=abort)
        if abort is False and 'purpose' in holder:
            assert purpose is holder['purpose']
            called.append(True)
            if field=='pending':purpose._abort_pending=substitute
            else:purpose._abort=substitute
        return result
    monkeypatch.setattr(NoisePurpose,'_fence',fence)
    with active_provider(tmp_path) as (r,p,source,op,runner,events):
        holder['purpose']=p._purpose
        assert p.close() is False
        pending=p._purpose._abort_pending;rows=copy.deepcopy(r.state.rows)
        assert r.hardware.close(2) is False and called==[True]
        assert p._purpose._abort_pending is (substitute if field=='pending' else pending)
        assert p._owner._registry._cancellations[id(p._owner)] is pending
        assert not source.closed and not p._enrollment._closed and p._uncertain
        assert op.active is None and not runner.retired and r.state.rows==rows


class DelegatedAccounting(ModeledAccounting):
    def close_from_registry(self,permit):
        assert permit.original_owner==self.provider._process and permit.resource is self.r.hardware
        with pytest.raises(ForwardChildrenError):permit.consume(self.provider._purpose)
        assert not permit._used
        self._busy=1
        try:
            with pytest.raises(ForwardChildrenError):permit.consume(self)
            assert not permit._used
        finally:self._busy=0
        assert permit.consume(self) is True
        with pytest.raises(ForwardChildrenError):permit.consume(self)
        self.permit=permit
        return self.close()


@pytest.mark.parametrize('delegated',[False,True])
def test_same_pid_foreign_thread_uses_exact_delegation_or_refuses(tmp_path,delegated):
    ready=threading.Event();release=threading.Event();holder={};errors=[]
    def source_change(source):
        if delegated:source.__class__=DelegatedAccounting
    def producer():
        try:
            with active_provider(tmp_path,source_change=source_change) as values:
                holder['values']=values;ready.set();assert release.wait(15)
        except BaseException as error:errors.append(error);ready.set()
    thread=threading.Thread(target=producer);thread.start()
    try:
        assert ready.wait(15) and not errors
        r,p,source,op,runner,events=holder['values'];original=p._process
        assert r.hardware.close(3) is delegated
        assert p._process==p._purpose._owner==p._enrollment._pid_thread==original
        assert op.active is None and op._cancelled is runner and not runner.retired
        assert source.closed is delegated
        if delegated:assert source.permit._used and p.close() is False
    finally:release.set();thread.join(15)
    assert not thread.is_alive() and not errors


def test_actual_radio_creator_thread_requirement_is_not_bypassed(tmp_path,monkeypatch):
    with factory250(tmp_path,monkeypatch) as r:
        hardware_close_ready(r.hardware);runner=r.stage.prepare(payload=r.packet);results=[]
        thread=threading.Thread(target=lambda:results.append(r.hardware.close(2)))
        thread.start();thread.join(4)
        assert not thread.is_alive() and results==[False] and not r.hardware._closed
        assert r.op.active is runner and not runner.retired and r.op.position==0
        assert r.owner._registry._children[0].unknown
        r.stage._cleanup(time.monotonic()+3) # Only the actual creator may finish containment.
        assert r.transport.fd is r.transport.lock_fd is None and r.firmware['frames']==[]
