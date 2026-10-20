# SPDX-License-Identifier: Apache-2.0
"""Bounded native forward work tracking, not physical retirement authority.

Factory entry is tracked after protected prepayment and before open/ARM. Root
shutdown fences registration and contains owned children outside the hardware
mutex. Native containment and terminal observers must be independently qualified;
flow completion/cancellation, absent processes or a boolean type supply no proof.
Unknowns and prepaid debt remain retained even if physical containment succeeds.
"""
import os
import threading
import time
from lantern_meshticulum.sage.forward_budget import ChildPlan
from lantern_meshticulum.sage.mesh_inbox import _canonical

class ForwardChildrenError(RuntimeError):
    def __init__(self):super().__init__('forward_children_unavailable')

def require(value):
    if not value:raise ForwardChildrenError()

class ForwardClosePermit:
    """One registered component cleanup, never live/transport authority."""
    __slots__=('_registry','_owner','_target','_generation','_deadline','_original_owner',
               '_resource','_thread','_refs','_used','_proof')
    def __init__(self,registry,owner,target,generation,deadline):
        self._registry,self._owner,self._target=registry,owner,target
        self._generation,self._deadline=generation,deadline
        self._original_owner=registry._owner_threads[id(owner)]
        self._resource=registry._resource
        self._thread=os.getpid(),threading.get_ident()
        self._refs=(registry,owner,target,generation,self._resource)
        self._proof=registry._cleanup_proof(owner,target)
        self._used=False
    @property
    def original_owner(self):return self._original_owner
    @property
    def resource(self):return self._resource
    def consume(self,target):
        require(type(self._registry) is ForwardChildren and self._registry is self._refs[0])
        return ForwardChildren.consume_cleanup_permit(self._registry,self,target)

class ForwardCancellationPending:
    """Registered cleanup ownership, not a successful abort or permission."""
    __slots__=('_registry','_owner','_operation','_runner','_refs','_accepted','_completed')
    def __init__(self,registry,owner,operation,runner):
        self._registry,self._owner,self._operation,self._runner=registry,owner,operation,runner
        self._refs=(registry,owner,operation,runner)
        self._accepted=None;self._completed=False
    def accept(self,purpose,abort):
        require(type(self._registry) is ForwardChildren and self._registry is self._refs[0])
        return ForwardChildren.accept_cancellation(self._registry,self,purpose,abort)

class _EndpointCleanup:
    """Creator-bound local components; no live observations during drain."""
    def __init__(self,owner,provider):
        self.refs=(owner,provider,provider._purpose,provider._enrollment,provider.accounting,
                   provider._store,owner.resource,provider._abort_callback)
        self.methods=tuple((obj,name,getattr(type(obj),name,None)) for obj,names in (
            (owner,('close','_close_components','_cleanup_bound','_registry_close','_registry_finish_close')),
            (provider,('close','_abort','_cleanup_busy','_registry_finish_close')),
            (provider._purpose,('_fence','close','close_from_registry')),
            (provider._enrollment,('close','close_from_registry')),
            (provider.accounting,('observe','close','close_from_registry')))
            for name in names)
    def bound(self):
        owner,p,purpose,enrollment,source,store,resource,abort=self.refs
        require(p._owner is owner and p.resource is owner.resource is resource
            and p._purpose is purpose and p._enrollment is enrollment and p.accounting is source
            and p._store is store and p._abort_callback is abort
            and abort.__self__ is p and abort.__func__ is type(p)._abort
            and purpose._abort is abort and purpose.store is store and enrollment.store is store
            and p._process==purpose._owner==enrollment._pid_thread==owner._owner
            and p._process[0]==os.getpid()
            and (p._operation is None or p._operation is owner._operation))
        for obj,name,method in self.methods:
            require(name not in obj.__dict__ and getattr(type(obj),name,None) is method)
        source_methods={name:method for obj,name,method in self.methods if obj is source}
        require(type(p._source_cleanup) is tuple and len(p._source_cleanup)==2
            and p._source_cleanup[0] is source and p._source_cleanup[1] is source_methods['close']
            and p._source_registry_close is source_methods['close_from_registry'])
        return self

class _Child:
    def __init__(self,owner,plan,contain,terminal):
        self.owner,self.plan,self.contain,self.terminal=owner,plan,contain,terminal
        self.bindings=(owner,plan,contain,terminal)
        self.runner=None;self.refs=None
        self.opening=True;self.open_thread=threading.get_ident()
        self.retired=False;self.unknown=False;self.observing=False;self.observe_thread=None
        self.paid_refs=self.paid_value=None

class ForwardChildren:
    def __init__(self,resource):
        self.resource=self._resource=resource;self._pid=os.getpid()
        self._condition=threading.Condition();self._owners={};self._children=[]
        self._owner_threads={}
        self._closing=self._busy=self._uncertain=False
        self._close_permits={};self._drain_thread=None
        self._consumed_owner_permits={};self._component_permits={};self._component_targets=set()
        self._drain_generation=self._drain_deadline=None
        self._endpoints={};self._endpoint_pins={};self._cancellations={};self._endpoint_joined=set()
        self._accepted_cancellations={};self._completed_cancellations=set()
    def __repr__(self):return '<ForwardChildren>'
    def _bound(self):
        require(os.getpid()==self._pid and self.resource is self._resource
            and getattr(self.resource,'_forward_children',None) is self)
    def register(self,owner):
        from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner
        from lantern_meshticulum.sage.forward_native_relay import ForwardNativeRelay
        self._bound()
        with self._condition:
            require(not self._closing and not self._uncertain and not self.resource._closing and type(owner) in (ForwardNativeOwner,ForwardNativeRelay)
                and owner.resource is self.resource and len(self._owners)<32 and id(owner) not in self._owners)
            self._owners[id(owner)]=owner
            self._owner_threads[id(owner)]=owner._owner
    def available(self,owner):
        self._bound()
        with self._condition:
            require(not self._closing and not self._uncertain and self._owners.get(id(owner)) is owner)
        return True
    def bind_endpoint(self,owner,provider):
        from .forward_native_owner import ForwardNativeOwner
        from .forward_native_provider import ForwardNativeProvider
        self.available(owner)
        require(type(owner) is ForwardNativeOwner and type(provider) is ForwardNativeProvider
            and owner._owner==(os.getpid(),threading.get_ident()) and provider._purpose._factory_active
            and id(owner) not in self._endpoints)
        bundle=_EndpointCleanup(owner,provider).bound()
        self._endpoints[id(owner)]=bundle
        self._endpoint_pins[id(owner)]=(bundle,bundle.refs,bundle.methods)
    def _endpoint(self,owner):
        bundle=self._endpoints.get(id(owner))
        try:
            if bundle is None:
                require(id(owner) not in self._endpoint_pins);return None
            original,refs,methods=self._endpoint_pins[id(owner)]
            require(bundle is original and bundle.refs is refs and bundle.methods is methods)
            return bundle.bound()
        except BaseException:
            self._uncertain=True;raise
    def _paid_bound(self,child):
        self._runner_bound(child);r=child.runner
        require(r is not None and child.paid_refs is not None)
        b=r.operation.budget;a=r.admission
        require(type(child.paid_refs) is tuple and len(child.paid_refs)==9
            and all(x is y for x,y in zip(child.paid_refs,
            (r.operation,a,b,r.plan,b._permits,r.operation.session,b.store,b.limits,b.prior)))
            and a.owner is b and a.plan is r.plan is child.plan
            and b._permits.get(id(a)) is a and a.bound is True
            and child.paid_value==(_canonical(r.plan.to_dict()),_canonical(b.binding),b._body,
                _canonical((b._revision,a.revision,r.operation.position))))
    def cancellation_pending(self,owner):
        from .forward_native_owner import ForwardNativeOwner
        self._bound();require(type(owner) is ForwardNativeOwner and self._owners.get(id(owner)) is owner)
        owner._cleanup_bound()
        op=owner._operation
        require(owner._closed and op is not None and op.active is not None
            and not owner._uncertain and not op._uncertain and not op.budget._uncertain)
        children=[c for c in self._children if c.owner is owner and c.runner is op.active]
        require(len(children)==1 and not children[0].opening and not children[0].unknown)
        self._paid_bound(children[0]);self._endpoint(owner)
        pending=self._cancellations.get(id(owner))
        if pending is None:
            pending=ForwardCancellationPending(self,owner,op,op.active);self._cancellations[id(owner)]=pending
        require(type(pending) is ForwardCancellationPending and all(a is b for a,b in zip(
            pending._refs,(self,owner,op,op.active))) and pending._registry is self
            and pending._owner is owner and pending._operation is op and pending._runner is op.active)
        return pending
    def accept_cancellation(self,pending,purpose,abort):
        require(type(pending) is ForwardCancellationPending
            and self._cancellations.get(id(pending._owner)) is pending
            and self.cancellation_pending(pending._owner) is pending)
        bundle=self._endpoint(pending._owner)
        require(bundle is not None and bundle.refs[2] is purpose and bundle.refs[7] is abort
            and pending._accepted is None and not pending._completed)
        pending._accepted=purpose
        self._accepted_cancellations[id(pending._owner)]=(pending,purpose,abort)
        return True
    def begin(self,owner,plan,*,contain,terminal):
        from lantern_meshticulum.sage.forward_native_relay import ForwardNativeRelay
        self.available(owner)
        require(type(plan) is ChildPlan and callable(contain) and callable(terminal))
        if type(owner) is ForwardNativeRelay:
            require(owner._registry_prepaid(plan) is True)
        else:
            op=owner._operation
            require(op is not None and op._busy and op.expected()==(plan.kind,plan.sends)
                and any(a.plan==plan and not a.bound for a in op.budget._permits.values()))
        with self._condition:
            require(not self._closing and not self._uncertain and not self.resource._closing and len(self._children)<160
                and not any(c.owner is owner and c.plan.slot==plan.slot for c in self._children))
            child=_Child(owner,plan,contain,terminal);self._children.append(child);return child
    def _child(self,child):
        self._bound();require(type(child) is _Child and any(c is child for c in self._children)
            and all(a is b for a,b in zip(child.bindings,(child.owner,child.plan,child.contain,child.terminal))))
    def bind(self,child,runner):
        from lantern_meshticulum.sage.forward_operation import ForwardStageRunner
        from lantern_meshticulum.sage.forward_native_relay import ForwardNativeRelay,RelayChild
        self._child(child)
        if type(child.owner) is ForwardNativeRelay:
            require(type(runner) is RelayChild and child.owner._registry_bind(child.plan,runner) is True)
        else:
            require(type(runner) is ForwardStageRunner and runner.operation is child.owner._operation
                and runner.plan==child.plan and runner.admission.owner is runner.operation.budget
                and runner.admission.bound and runner.operation.active is runner)
        with self._condition:
            require(child.opening and child.open_thread==threading.get_ident() and child.runner is None)
            child.runner=runner
            t=runner.transfer;child.refs=(runner,t,t.flow,t.flow.worker,t.authority,t.authority.grant)
            if type(child.owner) is not ForwardNativeRelay:
                b=runner.operation.budget;a=runner.admission
                child.paid_refs=(runner.operation,a,b,runner.plan,b._permits,runner.operation.session,b.store,b.limits,b.prior)
                child.paid_value=(_canonical(runner.plan.to_dict()),_canonical(b.binding),b._body,
                                  _canonical((b._revision,a.revision,runner.operation.position)))
            child.opening=False;self._condition.notify_all()
            if self._closing:child.unknown=True;raise ForwardChildrenError()
    def failed(self,child):
        self._child(child)
        with self._condition:
            child.opening=False;child.unknown=True;self._uncertain=True;self._condition.notify_all()
    def _runner_bound(self,child):
        self._child(child)
        if child.runner is not None:
            r=child.runner;t=r.transfer
            require(all(a is b for a,b in zip(child.refs,(r,t,t.flow,t.flow.worker,t.authority,t.authority.grant))))
    def active_owned(self,owner,runner):
        self._bound()
        with self._condition:
            require(any(c.owner is owner and c.runner is runner and not c.opening
                        and not c.unknown for c in self._children))
        return True
    def owns_runner(self,owner,runner):
        self.available(owner)
        with self._condition:
            matches=[c for c in self._children if c.owner is owner and c.runner is runner]
            require(len(matches)==1 and not matches[0].opening and not matches[0].unknown)
        self._runner_bound(matches[0]);return True
    def retired(self,runner):
        self._bound()
        with self._condition:
            matches=[c for c in self._children if c.runner is runner]
            require(len(matches)==1 and not self._closing)
            child=matches[0]
            if child.observing:child.unknown=True;self._uncertain=True;raise ForwardChildrenError()
            child.observing=True;child.observe_thread=threading.get_ident()
        try:
            self._runner_bound(child);require(not child.opening and not child.unknown and runner.retired)
            require(child.terminal(child.plan,runner) is True);self._runner_bound(child)
            with self._condition:
                require(not self._closing and not child.unknown);child.retired=True
            return True
        except BaseException:
            child.unknown=True;self._uncertain=True;raise
        finally:
            with self._condition:
                child.observing=False;child.observe_thread=None;self._condition.notify_all()
    def consume_close_permit(self,owner):
        self._bound()
        with self._condition:
            require(self._closing and self._busy and self._drain_thread==threading.get_ident()
                and self._close_permits.pop(id(owner),None) is owner)
            self._consumed_owner_permits[id(owner)]=owner
        return True
    def _cleanup_current(self,owner,target):
        from lantern_meshticulum.sage.forward_native_relay import ForwardNativeRelay
        from .forward_native_owner import ForwardNativeOwner
        self._bound()
        require(type(owner) in (ForwardNativeRelay,ForwardNativeOwner) and self._closing and self._busy
            and self._drain_thread==threading.get_ident() and self._drain_generation is not None
            and time.monotonic()<self._drain_deadline
            and self._owners.get(id(owner)) is self._consumed_owner_permits.get(id(owner)) is owner
            and owner._owner==self._owner_threads[id(owner)] and owner._owner[0]==os.getpid()
            and owner.resource is self.resource and owner._registry is self and owner._closed)
        children=[c for c in self._children if c.owner is owner]
        require(not any(c.opening or c.observing or c.runner is not None and c.runner._executing for c in children))
        if type(owner) is ForwardNativeOwner:
            require(not self._uncertain and not self._endpoint_busy(owner)
                and all(c.retired and not c.unknown for c in children))
            owner._cleanup_bound();bundle=self._endpoint(owner)
            if target is owner._operation:
                matches=[c for c in children if c.runner is owner._operation.active]
                require(len(matches)==1);self._paid_bound(matches[0]);return
            require(bundle is not None and id(owner) in self._endpoint_joined
                and any(target is v for v in bundle.refs[1:5]))
            self._local_joined(owner)
            require(not bundle.refs[1]._uncertain)
            purpose=bundle.refs[2];pending=self._cancellations.get(id(owner))
            accepted=self._accepted_cancellations.get(id(owner))
            if accepted is not None:
                require(pending is accepted[0] and purpose is accepted[1] and bundle.refs[7] is accepted[2]
                    and pending._accepted is purpose
                    and pending._completed is (id(pending) in self._completed_cancellations)
                    and purpose._abort_pending is (None if pending._completed else pending)
                    and owner._operation._cancelled is pending._runner)
            else:require(purpose._abort_pending is None)
            if target is bundle.refs[3] or target is bundle.refs[4]:
                require(purpose._disabled and purpose._key is None and purpose._abort_called
                    and purpose._abort_pending is None and not purpose._uncertain and not purpose._close_failed
                    and all(s._closed and not s._busy and s._s is None and s._tx is None and s._rx is None
                            for s in purpose._sessions))
            if target is bundle.refs[4]:
                require(bundle.refs[3]._closed and not bundle.refs[3]._uncertain)
            return
        require(not owner._draining_busy())
        require(target is owner._relay_ref or target is owner._source_close[0])
        if target is owner._source_close[0]:
            busy=getattr(target,'_busy',False)
            require(all(c.retired for c in children) and type(busy) in (bool,int) and busy==0)
    def _cleanup_proof(self,owner,target):
        from .forward_native_owner import ForwardNativeOwner
        if type(owner) is not ForwardNativeOwner:return None
        if target is owner._operation:
            child=next(c for c in self._children if c.owner is owner and c.runner is target.active)
            return (child,child.runner,child.plan,child.paid_refs,child.paid_value)
        return self._endpoint(owner)
    def cleanup_permit(self,owner,target):
        with self._condition:
            self._cleanup_current(owner,target)
            key=id(owner),id(target)
            require(key not in self._component_targets)
            permit=ForwardClosePermit(self,owner,target,self._drain_generation,self._drain_deadline)
            self._component_targets.add(key);self._component_permits[id(permit)]=permit
            return permit
    def consume_cleanup_permit(self,permit,target):
        with self._condition:
            require(type(permit) is ForwardClosePermit and self._component_permits.get(id(permit)) is permit
                and not permit._used and target is permit._target
                and all(a is b for a,b in zip(permit._refs,(self,permit._owner,target,self._drain_generation,self.resource)))
                and permit._original_owner==self._owner_threads[id(permit._owner)]
                and permit._thread==(os.getpid(),threading.get_ident())
                and permit._deadline==self._drain_deadline)
            self._cleanup_current(permit._owner,target)
            proof=self._cleanup_proof(permit._owner,target)
            if type(proof) is tuple:
                require(type(permit._proof) is tuple and len(proof)==len(permit._proof)
                    and all(a is b for a,b in zip(proof,permit._proof)))
            else:require(proof is permit._proof)
            permit._used=True;del self._component_permits[id(permit)]
            bundle=self._endpoints.get(id(permit._owner))
            pending=self._cancellations.get(id(permit._owner))
            if bundle is not None and target is bundle.refs[2] and pending is not None:
                pending._completed=True
                self._completed_cancellations.add(id(pending))
            return True
    def _endpoint_busy(self,owner):
        composition=owner._composition;op=owner._operation;budget=owner._budget
        bundle=self._endpoint(owner)
        return (owner._held or owner._busy or owner._constructing or owner._operation_observing
            or budget is not None and (budget._busy or budget._guarding)
            or op is not None and (op._busy or op._action_observing)
            or composition is not None and (composition._busy or composition._crypto_observing
                or composition._constructing or composition.journal._busy
                or composition.capture._busy is not None
                or composition.session is not None and composition.session._busy)
            or bundle is not None and bundle.refs[1]._cleanup_busy())
    def endpoint_joined(self,owner):
        self._bound();owner._cleanup_bound()
        require(self._closing and self._busy and self._drain_thread==threading.get_ident()
            and self._consumed_owner_permits.get(id(owner)) is owner and not self._endpoint_busy(owner)
            and (owner._operation is None or owner._operation.active is None)
            and all(c.retired and not c.unknown for c in self._children if c.owner is owner))
        self._local_joined(owner)
        self._endpoint_joined.add(id(owner))
    def _local_joined(self,owner):
        owner._cleanup_bound()
        require(not owner._uncertain and type(owner._cleanup_joined) is tuple
            and len(owner._cleanup_joined)==3 and all(v is True for v in owner._cleanup_joined))
        op=owner._operation;b=owner._budget;r=owner._composition
        require(op is None or (op._closed and op.active is None and not op._uncertain
            and op.session._closed and not op.session._busy))
        require(b is None or (b._closed and not b._uncertain and not b._busy))
        require(r is None or (r._closed and not r._uncertain and not r._busy
            and r.journal._closed and not r.journal._uncertain and not r.journal._busy
            and r.capture._closing and not r.capture._uncertain and r.capture._busy is None
            and r.capture._known in ('idle','captured','processed')))
        pending=self._cancellations.get(id(owner))
        if pending is not None:
            require(type(pending) is ForwardCancellationPending and type(pending._refs) is tuple
                and len(pending._refs)==4 and all(a is b for a,b in zip(pending._refs,
                    (self,owner,op,op._cancelled))) and pending._registry is self
                and pending._owner is owner and pending._operation is op and pending._runner is op._cancelled)
            matches=[c for c in self._children if c.owner is owner and c.runner is pending._runner]
            require(len(matches)==1 and matches[0].retired and not matches[0].unknown)
            self._paid_bound(matches[0])
    def close(self,timeout):
        """Separate containment, never normal retire/fresh control dispatch.

        The callback deadline covers all owners/children. A still-opening factory
        or an uncertain logical owner prevents success, even after containment.
        Call before acquiring HardwareRuntime's shared mutex.
        """
        require(type(timeout) in (int,float) and 0<=timeout<=60)
        self._bound();deadline=time.monotonic()+timeout
        with self._condition:
            self._closing=True
            if self._busy:self._uncertain=True;return False
            self._busy=True;owners=list(self._owners.values())
        result=True
        try:
            # Fencing first interrupts the original producers. No permission is
            # recreated to finish an ACK or send application data during drain.
            for owner in owners:owner.fence()
            def active(owner):
                from lantern_meshticulum.sage.forward_native_relay import ForwardNativeRelay
                if type(owner) is ForwardNativeRelay:return owner._draining_busy()
                return self._endpoint_busy(owner)
            with self._condition:
                if (any(c.opening and c.open_thread==threading.get_ident()
                        or c.observing and c.observe_thread==threading.get_ident() for c in self._children)
                    or any(active(o) and o._owner==(os.getpid(),threading.get_ident()) for o in owners)):
                    return False
                while any(c.opening or c.observing or c.runner is not None and c.runner._executing for c in self._children) or any(active(o) for o in owners):
                    remaining=deadline-time.monotonic()
                    if remaining<=0:return False
                    self._condition.wait(min(.01,remaining))
                children=list(self._children)
            # Only after dispatch/factory/crypto/protected writes have unwound
            # may logical close or physical containment touch their resources.
            with self._condition:
                self._drain_thread=threading.get_ident()
                self._close_permits={id(o):o for o in owners}
                self._drain_generation=object();self._drain_deadline=deadline
            for owner in owners:
                try:
                    closed=owner._registry_close(self)
                    if closed is not True:
                        require(type(closed) is ForwardCancellationPending
                            and self.cancellation_pending(owner) is closed)
                except BaseException:
                    from .forward_native_owner import ForwardNativeOwner
                    if type(owner) is ForwardNativeOwner:self._uncertain=True
                    result=False
            for child in children:
                if time.monotonic()>=deadline:return False
                if child.retired:
                    if child.unknown:result=False
                    continue
                try:
                    child.observing=True;child.observe_thread=threading.get_ident()
                    self._runner_bound(child)
                    require(child.contain(child.plan,child.runner,deadline) is True)
                    self._runner_bound(child)
                    require(child.terminal(child.plan,child.runner) is True)
                    self._runner_bound(child);child.retired=True
                except BaseException:
                    child.unknown=True;self._uncertain=True;result=False
                finally:
                    child.observing=False;child.observe_thread=None
                if child.unknown:result=False
            for owner in owners:
                try:
                    if owner._registry_finish_close(self) is not True:result=False
                except BaseException:
                    from .forward_native_owner import ForwardNativeOwner
                    if type(owner) is ForwardNativeOwner:self._uncertain=True
                    result=False
            return result and not self._uncertain and time.monotonic()<deadline
        finally:
            with self._condition:
                self._close_permits.clear();self._drain_thread=None
                self._consumed_owner_permits.clear();self._component_permits.clear();self._component_targets.clear()
                self._endpoint_joined.clear()
                self._drain_generation=self._drain_deadline=None
                self._busy=False;self._condition.notify_all()

def attach(owner):
    """Called only by an already authenticated native owner, before any claims."""
    h=owner.resource
    require(h._mutex.acquire(blocking=False))
    try:
        require(not h._closed and not h._closing)
        registry=getattr(h,'_forward_children',None)
        if registry is None:
            registry=ForwardChildren(h);h._forward_children=registry
        h._forward_admitted=True
        require(type(registry) is ForwardChildren and registry.resource is h)
        registry.register(owner);return registry
    finally:h._mutex.release()
