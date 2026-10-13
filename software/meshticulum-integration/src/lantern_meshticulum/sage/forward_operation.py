# SPDX-License-Identifier: Apache-2.0
"""Five-phase endpoint coordinator and explicitly admitted private stage loop.

Native factories must prepay child maxima BEFORE ARM/socket construction, own
actual direction/peer/purpose policy and terminal retirement. This module creates
no grants, workers, keys, listener, lease authority or physical measurements.
"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import os
import threading
from meshticulum.privacy.forward_session import (ForwardSession,inspect_record,H1,H2,CONFIRM_I,CONFIRM_R,DATA)
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.service_runner import _ServiceLoop
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.contracts.identity import ServiceKind
from lantern_meshticulum.sage.service_runner import _Held
from lantern_meshticulum.sage.forward_budget import ForwardBudget,ChildPlan,Cost,Admission
from lantern_meshticulum.sage.forward_receiver import ForwardReceiver
from lantern_meshticulum.sage.mesh_inbox import _canonical

class ForwardOperationError(RuntimeError):
    def __init__(self):super().__init__('forward_operation_unavailable')

def require(v):
    if not v:raise ForwardOperationError()

SEQUENCE=(H1,H2,CONFIRM_I,CONFIRM_R,DATA)

def describe_child(transfer,*,slot,kind,live=True):
    """Read actual enforced limits; native owner prepares same plan before ARM."""
    require(type(transfer) is ServiceTransfer and transfer.kind is ServiceKind.MESSAGE
        and transfer.format=='MPF2.record' and type(kind) is int and kind in SEQUENCE)
    f,a=transfer.flow,transfer.authority
    if type(f) is BenchFlow:
        require(type(f.journal) is AttemptJournal and f.worker.journal is f.journal
            and f.journal.max_attempts<=a.profile.max_boot_attempts
            and f.journal.max_airtime_us<=a.profile.max_boot_estimated_airtime_us
            and (not live or not f.journal.snapshot()['fenced']))
        cost=Cost(f.journal.max_attempts,f.journal.max_airtime_us)
        carrier='lora';profile=a.contract.route.profile_digest.hex()
        deadline=a.application_deadline_us
    else:
        from meshticulum.ip.flow import IpFlow
        from meshticulum.ip.journal import IpJournal
        require(type(f) is IpFlow and type(f.worker.journal) is IpJournal
            and f.worker.journal.grant.digest==a.grant.digest)
        caps=a.grant.limits;cost=Cost(ip_frames=caps.max_frames,
            tx_stream_bytes=caps.max_tx_stream_bytes,rx_stream_bytes=caps.max_rx_stream_bytes)
        carrier='tcp';profile=a.grant.digest.hex()
        deadline=int(a.deadlines.application_deadline_monotonic*1000000)
    return ChildPlan(slot,kind,f.sends,transfer.grant_digest.hex(),transfer.descriptor[1],transfer.descriptor[0],
        carrier,profile,a.plan['host_boot_id'],a.application_expires_epoch_us,
        deadline,cost,a.grant.max_frames)

class ForwardOperation:
    """One non-resumable crypto exchange; carrier retry reuses cached records."""
    def __init__(self,session,budget,*,sealed_record=None,receiver=None,native_check,operation_action_check=None):
        require(type(session) is ForwardSession and type(budget) is ForwardBudget
            and session.context==budget.context and session.deadline==budget.intention.application_deadline_monotonic_us
            and callable(native_check) and (operation_action_check is None or callable(operation_action_check)))
        local=session.context.initiator if session.initiator else session.context.responder
        require(budget.store.storage.actor.agent_id==local.public_key.hex()
            and budget.store.storage.actor.chain_id==local.chain_id)
        require(session.state=='new')
        if session.initiator:
            require(type(sealed_record) is bytes and len(sealed_record)==budget.intention.packet_bytes
                and hashlib.sha256(sealed_record).hexdigest()==budget.intention.packet_sha256)
        else:
            require(sealed_record is None and type(receiver) is ForwardReceiver
                and receiver.session is session and receiver.store is budget.store
                and receiver.intention==budget.intention)
        self.session,self.budget,self.receiver=session,budget,receiver
        self._sealed,self._native=sealed_record,native_check
        self._operation_action=self._operation_action_ref=operation_action_check
        self._action_refs=(session,budget,receiver,session.context,budget.intention,native_check,
                           session.authorize,session.clock,session.counters,budget.context)
        self._action_value=(session.context.digest,budget.context.digest,_canonical(budget.intention.to_dict()),
                            session.initiator,session.deadline)
        self._action_observing=False
        self.position=0;self.cached=None;self.active=None;self._busy=self._closed=self._uncertain=False
        self._owner=(os.getpid(),threading.get_ident())
        self._last_clock=None
    def __repr__(self):return '<ForwardOperation>'
    def expected(self):
        require(self.position<5)
        kind=SEQUENCE[self.position]
        sends=(self.position%2==0)==self.session.initiator
        return kind,sends
    def _available(self):require(not self._closed and not self._uncertain and not self.session._closed
        and self._owner==(os.getpid(),threading.get_ident())
        and self._operation_action is self._operation_action_ref)
    def _action_bound(self):
        self._available()
        require(all(a is b for a,b in zip(self._action_refs,
            (self.session,self.budget,self.receiver,self.session.context,self.budget.intention,self._native,
             self.session.authorize,self.session.clock,self.session.counters,self.budget.context)))
            and type(self.session.initiator) is bool and self.session.initiator is self._action_value[3]
            and type(self.session.deadline) is int and type(self.position) is int
            and self._action_value==(self.session.context.digest,self.budget.context.digest,
                                    _canonical(self.budget.intention.to_dict()),
                                    self.session.initiator,self.session.deadline))
    def _check_action(self,kind,sends):
        """One fresh combined observation at this boundary, never across it."""
        if self._action_observing:
            self._uncertain=True;raise ForwardOperationError()
        was_busy=self._busy
        try:
            self._action_bound()
            require(type(kind) is int and kind in SEQUENCE and type(sends) is bool
                and sends==((kind%2==1)==self.session.initiator))
            phase=(self.position,self.active,self.cached)
            self._action_observing=self._busy=True
            allowed=self._operation_action(self.session.context,self.budget.intention,kind,sends,
                                           self.session,self.session.authorize)
            self._action_bound();require(allowed is True)
            require(self.position==phase[0] and self.active is phase[1] and self.cached is phase[2])
            now=self.session.clock();self._action_bound()
            require(self.position==phase[0] and self.active is phase[1] and self.cached is phase[2]
                and type(now) is tuple and len(now)==2 and all(type(v) is int for v in now)
                and self.session.context.issued_epoch_us<=now[0]<self.session.context.expires_epoch_us
                and 0<=now[1]<self.session.deadline
                and (self._last_clock is None or all(a>=b for a,b in zip(now,self._last_clock))))
            self._last_clock=now
        except BaseException:
            self._uncertain=self._closed=True
            self._action_refs[0].close()
            raise
        finally:
            self._action_observing=False;self._busy=was_busy
    def _check(self,kind,sends):
        if self._operation_action_ref is not None:return self._check_action(kind,sends)
        self._available();allowed=self._native(self.session.context,kind,sends);self._available()
        require(allowed is True)
        self._available()
        authorized=self.session.authorize();self._available();require(authorized is True)
        now=self.session.clock();self._available()
        require(type(now) is tuple and len(now)==2 and all(type(v) is int for v in now)
            and self.session.context.issued_epoch_us<=now[0]<self.session.context.expires_epoch_us
            and 0<=now[1]<self.session.deadline
            and (self._last_clock is None or all(a>=b for a,b in zip(now,self._last_clock))))
        self._last_clock=now
    def _run(self,callback):
        entered=False
        try:
            self._available()
            if self._busy:self._uncertain=True;raise ForwardOperationError()
            self._busy=entered=True;r=callback();self._available();return r
        except BaseException as e:
            if entered:self._uncertain=True
            self._closed=True
            self.session.close()
            if not isinstance(e,Exception):raise
            raise ForwardOperationError() from None
        finally:
            if entered:self._busy=False
    def packet(self):
        def produce():
            kind,sends=self.expected();require(sends and self.active is None);self._check(kind,True)
            if self.cached is None:
                if kind==H1:self.cached=self.session.start()
                elif kind==CONFIRM_I:self.cached=self.session.confirm()
                elif kind==DATA:self.cached=self.session.seal(self._sealed)
                else:require(False) # H2/CONFIRM_R were produced by authenticated receive.
            actual,sid,counter=inspect_record(self.cached)
            require(actual==kind and sid==self.session.context.session_id
                and counter==(0 if kind in (H1,H2) else 1 if kind!=DATA else 2))
            self._check(kind,True);return self.cached
        return self._run(produce)
    def accept(self,packet,runner):
        def accept():
            kind,sends=self.expected()
            require(not sends and self.active is runner and type(runner) is ForwardStageRunner
                and runner._accepting and runner._executing and runner.wire_complete
                and runner.transfer.flow.flow.complete and not runner.transfer.flow.flow.failure
                and type(packet) is bytes and len(packet)==runner.plan.packet_bytes
                and hashlib.sha256(packet).hexdigest()==runner.plan.packet_sha256)
            self._check(kind,False)
            actual,sid,counter=inspect_record(packet)
            require(actual==kind and sid==self.session.context.session_id
                and counter==(0 if kind in (H1,H2) else 1 if kind!=DATA else 2))
            if kind in (H1,CONFIRM_I):self._check(SEQUENCE[self.position+1],True)
            if kind==H1:self.cached=self.session.respond(packet)
            elif kind==H2:self.session.finish(packet)
            elif kind==CONFIRM_I:self.cached=self.session.accept_confirmation(packet)
            elif kind==CONFIRM_R:self.session.verify_confirmation(packet)
            else:self.session.receive_into(packet)
            self._check(kind,False)
        return self._run(accept)
    def retired(self,runner):
        def retired():
            require(self.active is runner and runner.retired)
            if self.expected()[1]:self.cached=None
            self.active=None;self.position+=1
        return self._run(retired)
    def settle(self):
        require(self.position==5 and self.active is None and self.receiver is not None)
        return self._run(lambda:(self.receiver.import_pending(),self.receiver.settle())[1])
    def close(self):
        self._closed=True;self.session.close()
        if self.active is not None:self.active.transfer.cancel()
        return self.active is None and not self._busy and not self._uncertain

def prepare_stage(operation,plan,*,open_child,native_check,retire_child,payload=None):
    """Prepay before the trusted native factory can open/ARM the child.

    open_child must build this exact immutable plan and retain cleanup ownership
    on every construction/binding failure. A boolean/type is not RF authority.
    """
    require(type(operation) is ForwardOperation and type(plan) is ChildPlan and callable(open_child)
            and operation.expected()==(plan.kind,plan.sends) and operation.active is None)
    def prepare():
        operation._check(plan.kind,plan.sends)
        admission=operation.budget.pre_admit(plan)
        operation._check(plan.kind,plan.sends)
        operation.budget._available()
        operation.budget.check_pending(admission)
        operation._available()
        transfer=open_child(plan)
        operation._check(plan.kind,plan.sends)
        operation.budget._available()
        return ForwardStageRunner(transfer,operation,admission,native_check=native_check,
                                  retire_child=retire_child,payload=payload)
    return operation._run(prepare)

class ForwardStageRunner(_ServiceLoop):
    def __init__(self,transfer,operation,admission,*,native_check,retire_child,payload=None):
        require(type(transfer) is ServiceTransfer and type(operation) is ForwardOperation
            and type(admission) is Admission and admission.owner is operation.budget
            and callable(native_check) and callable(retire_child))
        operation._available();operation.budget._available()
        kind,sends=operation.expected();plan=admission.plan
        require(plan.kind==kind and plan.sends==sends and transfer.flow.sends==sends
            and operation.active is None and transfer.format=='MPF2.record'
            and operation.budget._lease is (operation.receiver._lease if operation.receiver else operation.budget._lease))
        require(describe_child(transfer,slot=plan.slot,kind=kind)==plan)
        require((sends and type(payload) is bytes and payload==operation.cached
                 and hashlib.sha256(payload).hexdigest()==plan.packet_sha256) or (not sends and payload is None))
        self.operation,self.admission,self.plan=operation,admission,plan
        self._native,self._retire=native_check,retire_child
        self._plan_value=_canonical(transfer.authority.plan)
        self.retired=False
        self._executing=False
        self._accepting=False
        self._refs=(transfer.flow,transfer.authority,transfer.flow.worker,transfer.flow.cipher,
            transfer.authority.grant,transfer.flow.journal if type(transfer.flow) is BenchFlow else transfer.flow.worker.journal,
            getattr(transfer.authority,'profile',None),getattr(transfer.authority,'contract',None),transfer.authority.local_peer)
        operation.budget.consume(admission)
        operation._available();operation.budget._available()
        super().__init__(transfer,payload=payload)
        operation.active=self
    def _bound(self,*,live=True):
        f,a=self.transfer.flow,self.transfer.authority
        current=(f,a,f.worker,f.cipher,a.grant,f.journal if type(f) is BenchFlow else f.worker.journal,
                 getattr(a,'profile',None),getattr(a,'contract',None),a.local_peer)
        require(len(current)==len(self._refs) and all(x is y for x,y in zip(current,self._refs))
            and f.cipher.grant is a.grant
            and _canonical(a.plan)==self._plan_value and f._service_adapter is self.transfer
            and describe_child(self.transfer,slot=self.plan.slot,kind=self.plan.kind,live=live)==self.plan)
        self.operation._check(self.plan.kind,self.plan.sends)
        self.operation.budget.check(self.admission)
        v=self._native(self.operation.session.context,self.plan,self.transfer);require(v is True)
        self.operation._available();self.operation.budget._available()
        require(not live or (not self.transfer.cancelled and not self.transfer.flow.closed))
        # Native callbacks may have replaced a previously checked object.
        current=(self.transfer.flow,self.transfer.authority,self.transfer.flow.worker,
            self.transfer.flow.cipher,self.transfer.authority.grant,
            self.transfer.flow.journal if type(self.transfer.flow) is BenchFlow else self.transfer.flow.worker.journal,
            getattr(self.transfer.authority,'profile',None),getattr(self.transfer.authority,'contract',None),self.transfer.authority.local_peer)
        require(len(current)==len(self._refs) and all(x is y for x,y in zip(current,self._refs))
                and self.transfer.flow.cipher.grant is self.transfer.authority.grant
                and _canonical(self.transfer.authority.plan)==self._plan_value)
        require(describe_child(self.transfer,slot=self.plan.slot,kind=self.plan.kind,live=live)==self.plan)
    @contextmanager
    def _held(self,*,retiring=False):
        self._bound()
        with _Held(self.operation.budget._lease()):
            self._bound();yield;self._bound(live=not retiring)
        self._bound(live=not retiring)
    def _prepare_receive(self):self._bound()
    def _send(self):
        with self._held():super()._send()
    def _dispatch(self):
        with self._held():return super()._dispatch()
    def _receive(self):
        self._bound()
        with self.transfer.authority.application_lease():packet=self.transfer._received()
        # No held native/application lease beneath endpoint journal operations.
        self._accepting=True
        try:self.operation.accept(packet,self)
        finally:self._accepting=False
        self._bound()
    def _act(self,method):
        if self._executing:
            self.operation.close()
            raise ForwardOperationError()
        self._executing=True
        try:
            result=method()
            self.operation._available();self.operation.budget._available()
            return result
        except BaseException:
            self.operation.close();self.transfer.cancel();raise
        finally:self._executing=False
    def start(self):return self._act(lambda:super(ForwardStageRunner,self).start())
    def step(self):return self._act(lambda:super(ForwardStageRunner,self).step())
    def retire(self):return self._act(self._retirement)
    def _retirement(self):
        """Owner flushes bounded final ACK/disarms/joins; no inferred retirement."""
        require(self.wire_complete and not self.retired)
        try:
            with self._held(retiring=True):
                if self.plan.carrier=='tcp' and not self.plan.sends:
                    require(self.transfer.flow.finish_receive_ack())
                require(self._retire(self.transfer) is True)
            self._bound(live=False);self.retired=True;self.transfer.cancel()
            self.operation.retired(self);return True
        except BaseException:
            self.state='failed';self.transfer.cancel();self.operation.close();raise
    def close(self):
        self.transfer.cancel()
        if not self.retired:self.operation.close()
        self.payload=None
        return self.retired
