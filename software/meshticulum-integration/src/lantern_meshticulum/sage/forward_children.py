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

class ForwardChildrenError(RuntimeError):
    def __init__(self):super().__init__('forward_children_unavailable')

def require(value):
    if not value:raise ForwardChildrenError()

class ForwardClosePermit:
    """One registered component cleanup, never live/transport authority."""
    __slots__=('_registry','_owner','_target','_generation','_deadline','_original_owner',
               '_resource','_thread','_refs','_used')
    def __init__(self,registry,owner,target,generation,deadline):
        self._registry,self._owner,self._target=registry,owner,target
        self._generation,self._deadline=generation,deadline
        self._original_owner=registry._owner_threads[id(owner)]
        self._resource=registry._resource
        self._thread=os.getpid(),threading.get_ident()
        self._refs=(registry,owner,target,generation,self._resource)
        self._used=False
    @property
    def original_owner(self):return self._original_owner
    @property
    def resource(self):return self._resource
    def consume(self,target):
        require(type(self._registry) is ForwardChildren and self._registry is self._refs[0])
        return ForwardChildren.consume_cleanup_permit(self._registry,self,target)

class _Child:
    def __init__(self,owner,plan,contain,terminal):
        self.owner,self.plan,self.contain,self.terminal=owner,plan,contain,terminal
        self.bindings=(owner,plan,contain,terminal)
        self.runner=None;self.refs=None
        self.opening=True;self.open_thread=threading.get_ident()
        self.retired=False;self.unknown=False;self.observing=False;self.observe_thread=None

class ForwardChildren:
    def __init__(self,resource):
        self.resource=self._resource=resource;self._pid=os.getpid()
        self._condition=threading.Condition();self._owners={};self._children=[]
        self._owner_threads={}
        self._closing=self._busy=self._uncertain=False
        self._close_permits={};self._drain_thread=None
        self._consumed_owner_permits={};self._component_permits={};self._component_targets=set()
        self._drain_generation=self._drain_deadline=None
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
        self._bound()
        require(type(owner) is ForwardNativeRelay and self._closing and self._busy
            and self._drain_thread==threading.get_ident() and self._drain_generation is not None
            and time.monotonic()<self._drain_deadline
            and self._owners.get(id(owner)) is self._consumed_owner_permits.get(id(owner)) is owner
            and owner._owner==self._owner_threads[id(owner)] and owner._owner[0]==os.getpid()
            and owner.resource is self.resource and owner._registry is self
            and owner._closed and not owner._draining_busy())
        children=[c for c in self._children if c.owner is owner]
        require(not any(c.opening or c.observing or c.runner is not None and c.runner._executing for c in children))
        require(target is owner._relay_ref or target is owner._source_close[0])
        if target is owner._source_close[0]:
            busy=getattr(target,'_busy',False)
            require(all(c.retired for c in children) and type(busy) in (bool,int) and busy==0)
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
            permit._used=True;del self._component_permits[id(permit)]
            return True
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
                composition=owner._composition;op=owner._operation;budget=owner._budget
                return (owner._held or owner._busy or owner._constructing
                    or budget is not None and budget._busy or op is not None and op._busy
                    or composition is not None and (composition._busy or composition.journal._busy
                        or composition.capture._busy is not None
                        or composition.session is not None and composition.session._busy))
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
                    if owner._registry_close(self) is not True:result=False
                except BaseException:result=False
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
            from lantern_meshticulum.sage.forward_native_relay import ForwardNativeRelay
            for owner in owners:
                if type(owner) is ForwardNativeRelay:
                    try:
                        if owner._registry_finish_close(self) is not True:result=False
                    except BaseException:result=False
            return result and not self._uncertain and time.monotonic()<deadline
        finally:
            with self._condition:
                self._close_permits.clear();self._drain_thread=None
                self._consumed_owner_permits.clear();self._component_permits.clear();self._component_targets.clear()
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
