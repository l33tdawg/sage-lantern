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
        self._closing=self._busy=self._uncertain=False
        self._close_permits={};self._drain_thread=None
    def __repr__(self):return '<ForwardChildren>'
    def _bound(self):
        require(os.getpid()==self._pid and self.resource is self._resource
            and getattr(self.resource,'_forward_children',None) is self)
    def register(self,owner):
        from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner
        self._bound()
        with self._condition:
            require(not self._closing and not self._uncertain and not self.resource._closing and type(owner) is ForwardNativeOwner
                and owner.resource is self.resource and len(self._owners)<32 and id(owner) not in self._owners)
            self._owners[id(owner)]=owner
    def available(self,owner):
        self._bound()
        with self._condition:
            require(not self._closing and not self._uncertain and self._owners.get(id(owner)) is owner)
        return True
    def begin(self,owner,plan,*,contain,terminal):
        self.available(owner)
        op=owner._operation
        require(type(plan) is ChildPlan and callable(contain) and callable(terminal)
            and op is not None and op._busy and op.expected()==(plan.kind,plan.sends)
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
        self._child(child)
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
            return result and not self._uncertain and time.monotonic()<deadline
        finally:
            with self._condition:
                self._close_permits.clear();self._drain_thread=None
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
