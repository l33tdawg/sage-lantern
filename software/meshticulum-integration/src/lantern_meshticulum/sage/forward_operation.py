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
from types import MethodType
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
from lantern_meshticulum.sage.message_storage import MessageStorageProbe

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
        deadline=a.application_deadline_us
    return ChildPlan(slot,kind,f.sends,transfer.grant_digest.hex(),transfer.descriptor[1],transfer.descriptor[0],
        carrier,profile,a.plan['host_boot_id'],a.application_expires_epoch_us,
        deadline,cost,a.grant.max_frames)

class ForwardOperation:
    """One non-resumable crypto exchange; carrier retry reuses cached records."""
    def __init__(self,session,budget,*,sealed_record=None,receiver=None,native_check,operation_action_check=None,
                 stage_read_check=None):
        require(type(session) is ForwardSession and type(budget) is ForwardBudget
            and session.context==budget.context and session.deadline==budget.intention.application_deadline_monotonic_us
            and callable(native_check) and (operation_action_check is None or callable(operation_action_check))
            and (stage_read_check is None or callable(stage_read_check)))
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
        self._stage_read_action=self._stage_read_action_ref=stage_read_check
        self._action_refs=(session,budget,receiver,session.context,budget.intention,native_check,
                           session.authorize,session.clock,session.counters,budget.context)
        self._action_value=(session.context.digest,budget.context.digest,_canonical(budget.intention.to_dict()),
                            session.initiator,session.deadline)
        self._action_observing=False
        self.position=0;self.cached=None;self.active=None;self._cancelled=None;self._busy=self._closed=self._uncertain=False
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
        and self._operation_action is self._operation_action_ref
        and self._stage_read_action is self._stage_read_action_ref)
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
    def check_bound_read(self,token,clock):
        """Only retained operation metadata; no crypto/phase authorization."""
        require(type(token) is _StageRead and token.operation is self)
        token.bound();token._clock_valid(clock);require(token.phase=='operation')
        self._action_bound();kind,sends=self.expected()
        require(type(kind) is int and type(sends) is bool
            and (kind,sends)==(token.plan.kind,token.plan.sends))
        token.fence()
        now=clock('operation',self.session.clock)
        token.bound()
        require(type(now) is tuple and len(now)==2 and all(type(v) is int for v in now)
            and self.session.context.issued_epoch_us<=now[0]<self.session.context.expires_epoch_us
            and 0<=now[1]<self.session.deadline
            and (self._last_clock is None or all(a>=b for a,b in zip(now,self._last_clock))))
        self._last_clock=now;token.phase='budget'
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
    def acknowledge_cancelled(self,permit,runner):
        """Join an exact contained child; never complete a protocol stage."""
        from .forward_children import ForwardClosePermit
        require(type(permit) is ForwardClosePermit and permit.original_owner==self._owner
            and os.getpid()==self._owner[0] and self._closed and self.session._closed
            and not self._busy and not self._action_observing and self._cancelled is None
            and type(runner) is ForwardStageRunner and self.active is runner
            and runner.operation is self and not runner._executing and not runner._accepting
            and not runner.retired and runner.transfer.cancelled and runner.transfer.flow.closed)
        require(permit.consume(self) is True)
        self._cancelled=runner;self.active=None;self.cached=None;runner.payload=None
        return True

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
        self._stage_read_refs=_select_stage_read(self)
        self._stage_read_refs_ref=self._stage_read_refs
        operation.budget.consume(admission)
        operation._available();operation.budget._available()
        super().__init__(transfer,payload=payload)
        operation.active=self
    def _bound(self,*,live=True):
        require(self._stage_read_refs is self._stage_read_refs_ref)
        f,a=self.transfer.flow,self.transfer.authority
        current=(f,a,f.worker,f.cipher,a.grant,f.journal if type(f) is BenchFlow else f.worker.journal,
                 getattr(a,'profile',None),getattr(a,'contract',None),a.local_peer)
        require(len(current)==len(self._refs) and all(x is y for x,y in zip(current,self._refs))
            and f.cipher.grant is a.grant
            and _canonical(a.plan)==self._plan_value and f._service_adapter is self.transfer
            and describe_child(self.transfer,slot=self.plan.slot,kind=self.plan.kind,live=live)==self.plan)
        token=None
        selected=self._stage_read_refs
        if selected is not None:
            # A terminal factory keeps the original full checks, but never
            # silently replaces the selected native callback.
            action,native,kind,method,code,*_=selected
            def native_bound():
                require(self._stage_read_refs is self._stage_read_refs_ref is selected
                    and self._native is native and native.__func__ is method and method.__code__ is code
                    and type(native.__self__) is kind and kind._native is method
                    and '_native' not in native.__self__.__dict__)
            native_bound()
        current=(selected is not None and not selected[1].__self__._retired
            and (self.plan.carrier!='lora' or not selected[1].__self__.adoption._closed))
        if current and self._executing and self.operation.active is self:
            action,native,*_=self._stage_read_refs
            token=action(self)
            require(type(token) is _StageRead and token.runner is self)
            binding=token._bindings;delegates=token._delegate_pins;token.bound(active=False)
        else:
            self.operation._check(self.plan.kind,self.plan.sends)
            self.operation.budget.check(self.admission)
            native=selected[1] if selected is not None else self._native
        if selected is not None:native_bound()
        v=native(self.operation.session.context,self.plan,self.transfer);require(v is True)
        if selected is not None:native_bound()
        if token is not None:
            require(token._bindings is binding and token._delegate_pins is delegates)
            for name,function,code,defaults,kwdefaults in delegates:
                require(getattr(token,name) is function and function.__code__ is code
                    and function.__defaults__ is defaults and function.__kwdefaults__ is kwdefaults)
            binding(False)
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


def _select_stage_read(runner):
    """Pin the closed native set; arbitrary/model callbacks stay legacy."""
    action=runner.operation._stage_read_action
    if action is None:return None
    from .forward_native_owner import ForwardNativeOwner, _STAGE_READ_OBSERVER
    from .forward_native_provider import _STAGE_NATIVE_METHODS, _stage_function_stamp
    require(type(action) is MethodType and type(action.__self__) is ForwardNativeOwner
        and action.__func__ is _STAGE_READ_OBSERVER)
    native=runner._native
    if type(native) is not MethodType:return None
    match=next((entry for entry in _STAGE_NATIVE_METHODS if type(native.__self__) is entry[0]),None)
    if match is None:return None
    kind,method,code,read,read_code=match
    require(native.__func__ is method and method.__code__ is code
        and kind._native is method and '_native' not in native.__self__.__dict__
        and native.__self__.owner is action.__self__)
    facts=None
    if runner.plan.carrier=='lora':
        factory=native.__self__;callback=factory.observer._device_facts
        require(type(callback) is MethodType and callback.__self__ is factory.observer
            and any(obj is factory.observer and name=='_device_facts' and original is callback.__func__
                    for obj,name,original in factory._native_method_refs))
        facts=(factory.observer,callback.__func__,_stage_function_stamp(callback.__func__))
    return action,native,kind,method,code,read,read_code,facts


class _StageRead:
    """One metadata interval, ending before the required native callback.

    This token is not permission. Only two original clock call sites can borrow
    Owner.time, once each; their permit is spent before external callbacks.
    """
    def __init__(self,owner,runner):
        from .forward_native_provider import (ForwardNativeProvider, _RADIO_NATIVE_CHECK, _STAGE_PROVIDER_METHODS,
            _STAGE_PROOF_METHODS, _STAGE_PROOF_GLOBALS, _stage_function_stamp, _STAGE_STAMP_CODE)
        from .forward_native_owner import _STAGE_OWNER_METHODS
        require(type(runner) is ForwardStageRunner and runner._executing and not runner.retired)
        op=runner.operation;b=op.budget;receiver=owner._composition
        provider_callback=owner._radio_native_check
        require(type(provider_callback) is MethodType and type(provider_callback.__self__) is ForwardNativeProvider
            and provider_callback.__func__ is _RADIO_NATIVE_CHECK)
        p=provider_callback.__self__;registry=owner._registry
        children=[c for c in registry._children if c.owner is owner and c.runner is runner]
        require(len(children)==1);child=children[0]
        registry._paid_bound(child)
        self.owner,self.runner,self.operation,self.budget,self.provider,self.plan=owner,runner,op,b,p,runner.plan
        self.phase='operation';self._active=False;self._finished=False;self._failed=False
        self._checking=False;self._clock_busy=False
        selected=runner._stage_read_refs
        require(selected is not None and selected==_select_stage_read(runner))
        action,native,kind,method,code,read,read_code,facts=selected;factory=native.__self__
        refs=(op,b,receiver,p,registry,child,runner.admission,runner.plan,runner.transfer,
              op.session,b.store,b._permits,b.limits,b.prior,b.key,b._lease,b._clock,op.session.clock,
              b._epoch,b._body,child.paid_refs,child.paid_value,owner._observer_refs,op.cached)
        value=(_canonical(runner.plan.to_dict()),_canonical(b.binding),b._revision,runner.admission.revision,
               op.position,op.cached,op.session.context.digest,_canonical(owner.intention.to_dict()),
               _canonical(asdict(b.limits)),_canonical(asdict(b.prior)))
        proof_objects=(factory,owner,p,p._purpose,p._enrollment)
        if self.plan.carrier=='lora':proof_objects+=(factory.observer,factory.adoption)
        proof_methods=tuple((obj,name,stamp) for obj in proof_objects for cls,entries in _STAGE_PROOF_METHODS
            if type(obj) is cls for name,stamp in entries)
        require(type(owner._exchange_key_check) is MethodType
            and owner._exchange_key_check.__self__ is p._purpose
            and owner._exchange_key_check.__func__ is type(p._purpose).exchange_check)
        key_refs=(p._purpose,p._enrollment,owner._exchange_key_check,p._purpose._exchange_enrollment)
        methods=tuple((obj,name,original,original_code) for obj,entries in (
            (owner,_STAGE_OWNER_METHODS),(p,_STAGE_PROVIDER_METHODS),
            (op,_STAGE_OPERATION_METHODS),(b,_STAGE_BUDGET_METHODS),
            (receiver,_STAGE_RECEIVER_METHODS),(b.store.storage,_STAGE_STORAGE_METHODS),
            (self,_STAGE_TOKEN_METHODS)) for name,original,original_code in entries)
        pending=None;spent=set();clock_running=False
        def clock(site,callback):
            nonlocal pending,clock_running
            bindings(True)
            expected=op.session.clock if site=='operation' else b._live
            require(site in ('operation','budget') and site==self.phase
                and type(callback) is MethodType and type(expected) is MethodType
                and callback.__self__ is expected.__self__ and callback.__func__ is expected.__func__
                and site not in spent and pending is None and not clock_running and not self._checking)
            spent.add(site);permit=object();pending=permit
            try:
                result=callback();bindings(True);require(pending is None)
                return result
            except BaseException:
                self._failed=owner._uncertain=True;raise
            finally:
                if pending is permit:pending=None
        def observe_time():
            nonlocal pending,clock_running
            bindings(True)
            if pending is None or clock_running or self._checking:
                self._failed=owner._uncertain=True;raise ForwardOperationError()
            pending=None;clock_running=self._clock_busy=True
            try:
                now=owner._observe(keys=False);bindings(True);return now
            except BaseException:
                self._failed=owner._uncertain=True;raise
            finally:clock_running=self._clock_busy=False
        def clock_valid(callback):
            if callback is not clock:
                self._failed=owner._uncertain=True;raise ForwardOperationError()
        def read_action():
            if owner._busy or op._busy or b._busy or owner._stage_read is not None or b._bound_read is not None:
                self._failed=owner._uncertain=True;raise ForwardOperationError()
            bindings(None)
            context=(read(factory,owner.context,self.plan,runner.transfer) if self.plan.carrier=='lora'
                     else read(factory,self))
            with context:
                self._active=True;owner._stage_read=self;owner._busy=op._busy=True
                try:
                    require(b.check_bound_read(runner.admission,self,clock) is True)
                    bindings('return');require(spent=={'operation','budget'} and pending is None)
                    self._finished=True;return self
                except BaseException:
                    self._failed=owner._uncertain=op._uncertain=b._uncertain=True;raise
                finally:
                    self._active=False
                    if owner._stage_read is self:owner._stage_read=None;owner._busy=False
                    op._busy=False
        def bindings(active):
            self._delegates()
            require(self._delegate_pins is delegates and self._clock_observer is observe_time and self._clock_valid is clock_valid
                and self._read_action is read_action and self._bindings is bindings
                and self._clock_busy is clock_running and not self._failed and self.owner is owner and self.runner is runner
                and self.operation is op and self.budget is b and self.provider is p and self.plan is runner.plan
                and runner._stage_read_refs is runner._stage_read_refs_ref is selected and runner._native is native
                and op._stage_read_action is op._stage_read_action_ref is action
                and action.__self__ is owner and type(owner).observe_stage_read is action.__func__
                and type(factory) is kind and type(factory)._native is method
                and native.__func__ is method and method.__code__ is code and '_native' not in factory.__dict__
                and getattr(type(factory),read.__name__) is read and read.__code__ is read_code
                and read.__name__ not in factory.__dict__
                and factory.owner is owner and factory.runner is runner and factory.transfer is runner.transfer
                and factory.profile.child is runner.plan and runner._executing and op.active is runner
                and not child.opening and not child.unknown and not child.retired
                and type(runner.plan.kind) is int and type(runner.plan.slot) is int
                and type(runner.plan.sends) is bool and type(runner.plan.cost) is Cost)
            require(all(a is c for a,c in zip(refs,(runner.operation,op.budget,owner._composition,
                provider_callback.__self__,owner._registry,child,runner.admission,runner.plan,runner.transfer,
                op.session,b.store,b._permits,b.limits,b.prior,b.key,b._lease,b._clock,op.session.clock,
                b._epoch,b._body,child.paid_refs,child.paid_value,owner._observer_refs,op.cached))))
            require(value==(_canonical(runner.plan.to_dict()),_canonical(b.binding),b._revision,runner.admission.revision,
                op.position,op.cached,op.session.context.digest,_canonical(owner.intention.to_dict()),
                _canonical(asdict(b.limits)),_canonical(asdict(b.prior))))
            require(all(a is c for a,c in zip(b._action_refs,
                (b.store,b.context,b.intention,b.key,b.limits,b.prior,b.slots)))
                and b._action_value==(b.context.digest,b.intention.to_dict(),b.boot_id,
                    asdict(b.limits),asdict(b.prior),_canonical(b.binding),b.record_id)
                and _canonical(b._action_value[3])==_canonical(asdict(b.limits))
                and _canonical(b._action_value[4])==_canonical(asdict(b.prior))
                and b.context is owner.context and b.intention is owner.intention
                and type(b.store.storage) is MessageStorageProbe and b.store.storage is receiver.probe
                and b.store.storage.client is receiver.client and receiver.client is owner._refs[2]
                and receiver._closed is False and receiver._uncertain is False
                and receiver.client._client.is_closed is False)
            require(_stage_function_stamp.__code__ is _STAGE_STAMP_CODE)
            for module,name,original in _STAGE_PROOF_GLOBALS:require(getattr(module,name) is original)
            if facts is not None:
                obj,original,stamp=facts
                require(factory.observer is obj and '_device_facts' not in obj.__dict__
                    and type(obj)._device_facts is original and original.__code__ is stamp[1][0]
                    and _stage_function_stamp(original)==stamp)
            for obj,name,stamp in proof_methods:
                require(name not in obj.__dict__ and getattr(type(obj),name) is stamp[0]
                    and stamp[0].__code__ is stamp[1][0] and _stage_function_stamp(stamp[0])==stamp
                    and (stamp[2] is None or stamp[2].__code__ is stamp[3][0]))
            require(p._purpose is key_refs[0] and p._enrollment is key_refs[1]
                and owner._exchange_key_check is key_refs[2] and p._purpose._exchange_enrollment is key_refs[3]
                and type(key_refs[3]) is MethodType and key_refs[3].__self__ is p
                and key_refs[3].__func__ is type(p)._exchange_enrollment_check)
            for obj,name,original,original_code in methods:
                require(name not in obj.__dict__ and getattr(type(obj),name) is original
                    and original.__code__ is original_code)
            require(type(b._clock) is MethodType and b._clock.__self__ is owner
                and b._clock.__func__ is type(owner).time
                and type(op.session.clock) is MethodType and op.session.clock.__self__ is receiver
                and op.session.clock.__func__ is type(receiver)._time
                and receiver._clock.__self__ is owner and receiver._clock.__func__ is type(owner).time)
            if active is True or active=='return':
                require(self._active and owner._stage_read is self and owner._busy
                    and op._busy and not op._action_observing
                    and (b._bound_read is self and b._busy if active is True else b._bound_read is None and not b._busy)
                    and child.observing and child.observe_thread==threading.get_ident()
                    and (factory._native_observing if self.plan.carrier=='lora'
                         else factory._stage_observation is self))
            elif active is None:
                require(not self._active and not self._finished and owner._stage_read is None
                    and b._bound_read is None and not owner._busy and not b._busy and not op._busy)
            else:
                require(self._finished and not self._active and owner._stage_read is None
                    and b._bound_read is None and not owner._busy and not b._busy and not op._busy)
            owner._available();op._action_bound();b._available();p._available()
            registry._paid_bound(child)
            require(owner._operation is p._operation is op and owner is p._owner
                and owner.consent.messaging_enabled is True and owner.consent.dispatch_enabled is True)
        self._bindings=bindings;self._clock_observer=observe_time
        self._clock_valid=clock_valid;self._read_action=read_action
        delegates=tuple((name,function,function.__code__,function.__defaults__,function.__kwdefaults__)
            for name,function in (('_bindings',bindings),('_clock_observer',observe_time),
                                  ('_clock_valid',clock_valid),('_read_action',read_action)))
        self._delegate_pins=delegates

    def _delegates(self):
        for name,function,code,defaults,kwdefaults in self._delegate_pins:
            require(getattr(self,name) is function and function.__code__ is code
                and function.__defaults__ is defaults and function.__kwdefaults__ is kwdefaults)

    def bound(self,*,active=True):
        try:self._delegates();self._bindings(active)
        except BaseException:
            self._failed=self.owner._uncertain=self.operation._uncertain=self.budget._uncertain=True
            raise

    def fence(self):
        binding=self._bindings;self.bound()
        if self._checking or self._clock_busy:
            self._failed=self.owner._uncertain=True;raise ForwardOperationError()
        self._checking=True
        try:
            require(self.provider._stage_native_check(self) is True)
            require(self._bindings is binding);self.bound()
        finally:self._checking=False

    def observe_time(self):
        self.bound()
        return self._clock_observer()

    def read(self):
        self._delegates()
        return self._read_action()


_STAGE_OPERATION_METHODS=tuple((n,getattr(ForwardOperation,n),getattr(ForwardOperation,n).__code__) for n in
    ('_available','_action_bound','check_bound_read'))
_STAGE_BUDGET_METHODS=tuple((n,getattr(ForwardBudget,n),getattr(ForwardBudget,n).__code__) for n in
    ('_available','_time','_live','_snapshot','check_bound_read'))
_STAGE_RECEIVER_METHODS=tuple((n,getattr(ForwardReceiver,n),getattr(ForwardReceiver,n).__code__) for n in ('_available','_time'))
_STAGE_STORAGE_METHODS=tuple((n,getattr(MessageStorageProbe,n),getattr(MessageStorageProbe,n).__code__)
    for n in ('_binding','snapshot'))
_STAGE_TOKEN_METHODS=tuple((n,getattr(_StageRead,n),getattr(_StageRead,n).__code__) for n in ('_delegates','bound','fence','observe_time','read'))
