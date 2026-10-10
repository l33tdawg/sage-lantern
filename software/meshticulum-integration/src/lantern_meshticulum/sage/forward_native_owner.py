# SPDX-License-Identifier: Apache-2.0
"""Borrowed native owner observations for a single approved MPF2 exchange.

No radio factory, identity enrollment, private key loading or activation occurs.
The root caller must grant the four exact forward scopes over the full original
approval digest. Existing receive grants and unlocked state cannot supply them.
Activation additionally requires native child tracking/drain and key enrollment.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import threading
import time
import uuid

from meshticulum.privacy.forward_session import ForwardContext,H1,H2,CONFIRM_I,CONFIRM_R,DATA
from lantern_meshticulum.sage.forward_receive_journal import ReceiveIntention
from lantern_meshticulum.sage.forward_receiver import ForwardReceiver
from lantern_meshticulum.sage.mesh_inbox import _canonical

READ='meshticulum_forward_read'
RECEIVE='meshticulum_forward_receive'
DISPATCH='meshticulum_forward_dispatch'
CONTROL='meshticulum_forward_control'
SCOPES=(READ,RECEIVE,DISPATCH,CONTROL)
DOMAIN=b'sage.lantern.forward-native-approval.v1\0'

class ForwardNativeOwnerError(RuntimeError):
    def __init__(self):super().__init__('forward_native_owner_unavailable')

def require(value):
    if not value:raise ForwardNativeOwnerError()

def host_boot():
    with Path('/proc/sys/kernel/random/boot_id').open('rb') as f:raw=f.read(38)
    require(len(raw)==37 and raw[-1:]==b'\n')
    value=raw[:-1].decode('ascii');require(str(uuid.UUID(value))==value and uuid.UUID(value).int!=0)
    return value

def clock():return time.time_ns()//1000,time.monotonic_ns()//1000

def approval_binding(context,intention,local_initiator,*,config_revision,broker_id,storage_epoch):
    """Data digest for the root approval request; never a bearer permission."""
    require(type(context) is ForwardContext and type(intention) is ReceiveIntention
        and type(local_initiator) is bool and type(config_revision) is str and len(config_revision)==64
        and type(broker_id) is str and len(broker_id)==32 and type(storage_epoch) is str)
    value=dict(context=context.digest.hex(),intention=intention.to_dict(),local_initiator=local_initiator,
        config_revision=config_revision,broker_id=broker_id,storage_epoch=storage_epoch)
    return hashlib.sha256(DOMAIN+_canonical(value)).hexdigest()

class ForwardNativeOwner:
    """Exact native resource/session binding, independent of a held lease.

    key_check remains an explicitly supplied dedicated-key enrollment observer.
    This module does not prove its provenance or register transport children.
    Permissions must be granted by the original authenticated root request.
    """
    def __init__(self,resource,authority,native_context,*,context,intention,local_initiator,
                 key_check,peer_key_check,boot_reader=host_boot,clock_reader=clock):
        from sage_lantern.mesh_daemon import HardwareRuntime
        from sage_lantern.mesh_community import MeshActionAuthority
        from sage_lantern.mesh_runtime import MeshUserContext
        require(type(resource) is HardwareRuntime and type(authority) is MeshActionAuthority
            and type(native_context) is MeshUserContext and type(context) is ForwardContext
            and type(intention) is ReceiveIntention and type(local_initiator) is bool
            and all(callable(f) for f in (key_check,peer_key_check,boot_reader,clock_reader)))
        self.resource,self.authority,self.native_context=resource,authority,native_context
        self.context,self.intention,self.local_initiator=context,intention,local_initiator
        self.local=context.initiator if local_initiator else context.responder
        self.remote=context.responder if local_initiator else context.initiator
        self._key_check,self._peer_key_check,self._boot,self._clock=key_check,peer_key_check,boot_reader,clock_reader
        self._composition=None;self._composition_ref=None;self._constructing=False
        self._budget=self._session=self._operation=None;self._budget_refs=None;self._registry=None
        self._owner=(os.getpid(),threading.get_ident());self._busy=self._closed=self._uncertain=False
        self._held=False;self._last_time=None
        self._native_refs=(resource,authority,native_context,context,intention,local_initiator,self.local,self.remote)
        self._refs=(resource.supervisor,resource.probe,resource.probe.client,resource.identity,
                    resource.identity._signing_key,resource._mutex)
        self.config_revision=hashlib.sha256(_canonical(resource.config)).hexdigest()
        self.consent=resource.consent();self.broker_id=resource.supervisor.broker_id
        self.storage_epoch=resource.probe.snapshot().epoch
        self._approval=approval_binding(context,intention,local_initiator,
            config_revision=self.config_revision,broker_id=self.broker_id,storage_epoch=self.storage_epoch)
        require(native_context.actor==resource.actor and native_context.approval_binding==self._approval
            and resource.actor.chain_id==self.local.chain_id and resource.actor.agent_id==self.local.public_key.hex()
            and resource.identity.agent_id==self.local.public_key.hex()
            and intention.authority_digest==context.binding_digest.hex()
            and intention.application_expires_epoch_us==context.expires_epoch_us)
        # Retain one stable callable object for receiver/budget lease identity.
        self.owner_lease=self._lease
        self.access_check()
        from lantern_meshticulum.sage.forward_children import attach
        self._registry=attach(self)
        self.access_check()

    def __repr__(self):return '<ForwardNativeOwner>'
    def _available(self):
        require(not self._closed and not self._uncertain and self._owner==(os.getpid(),threading.get_ident()))
        require(all(a is b for a,b in zip(self._native_refs,(self.resource,self.authority,self.native_context,
            self.context,self.intention,self.local_initiator,self.local,self.remote))))
        if self._registry is not None:self._registry.available(self)
        if self._composition is not None:require(self._composition is self._composition_ref)
        if self._budget is not None:
            receiver,store,journal,budget=self._budget_refs
            require(self._composition is receiver and self._budget is budget and receiver.store is store
                and receiver.journal is journal and journal.store is store and budget.store is store
                and journal.intention==budget.intention==self.intention
                and journal._lease is budget._lease is self.owner_lease)
        if self._session is not None:
            require(self._composition.session is self._session and self._session.counters is self._budget_refs[2])
        if self._operation is not None:
            require(self._operation.session is self._session and self._operation.budget is self._budget
                and self._operation.receiver is (None if self.local_initiator else self._composition))
        if self._operation is not None and self._operation.active is not None:
            self._registry.active_owned(self,self._operation.active)
        h=self.resource
        require(all(a is b for a,b in zip(self._refs,(h.supervisor,h.probe,h.probe.client,h.identity,
                h.identity._signing_key,h._mutex))))
        require(not h._closed and not h._closing and not h._uncertain
            and h.supervisor._resource is h and h.supervisor.broker_id==self.broker_id
            and h.supervisor.phase=='active' and h.probe.actor==h.actor
            and h.identity.agent_id==self.local.public_key.hex()
            and self.native_context.actor==h.actor and self.native_context.approval_binding==self._approval
            and hashlib.sha256(_canonical(h.config)).hexdigest()==self.config_revision)
    def _checked(self,callback,*args):
        self._available();v=callback(*args);self._available();return v
    def _observe(self,*,live=True):
        h=self.resource;self._available()
        require(self._checked(h.configured) is True and self._checked(h.active) is True
            and self._checked(h.consent)==self.consent
            and hashlib.sha256(_canonical(h.config)).hexdigest()==self.config_revision
            and self._checked(h.supervisor.resource_lifecycle_check) is True
            and self._checked(h.supervisor.verified_epoch)==self.storage_epoch
            and self._checked(h.probe.snapshot).epoch==self.storage_epoch
            and self._checked(h._epoch)==self.native_context.epoch
            and self._checked(self._boot)==self.intention.host_boot_id)
        # Epoch/unlock is only a lifecycle fence. The live authority record owns
        # the original authenticated read and full-exchange permissions.
        for scope in SCOPES:
            require(self._checked(self.authority.authorized,self.native_context,scope,None) is True)
        require(self.consent.messaging_enabled is True and self.consent.dispatch_enabled is True)
        peers=h.config['peers']
        require(any(p['chain_id']==self.remote.chain_id and p['agent_id']==self.remote.public_key.hex() for p in peers))
        local_key=self.context.initiator_noise_public_key if self.local_initiator else self.context.responder_noise_public_key
        require(self._checked(self._key_check,local_key,self.context.digest) is True)
        remote_key=self.context.responder_noise_public_key if self.local_initiator else self.context.initiator_noise_public_key
        require(self._checked(self._peer_key_check,self.remote,remote_key,self.context.digest) is True)
        now=self._checked(self._clock)
        require(type(now) is tuple and len(now)==2 and all(type(n) is int and n>=0 for n in now)
            and (self._last_time is None or all(a>=b for a,b in zip(now,self._last_time))))
        self._last_time=now
        if live:
            require(self.context.issued_epoch_us<=now[0]<self.intention.application_expires_epoch_us
                and now[1]<self.intention.application_deadline_monotonic_us)
        return now
    def _run(self,callback):
        entered=False
        try:
            self._available()
            if self._busy:self._uncertain=True;raise ForwardNativeOwnerError()
            self._busy=entered=True;result=callback();self._available();return result
        except BaseException as e:
            if entered:self._uncertain=True
            if not isinstance(e,Exception):raise
            raise ForwardNativeOwnerError() from None
        finally:
            if entered:self._busy=False
    def access_check(self):return self._run(lambda:(self._observe(),True)[1])
    storage_check=access_check
    def peer_check(self,peer):
        require(peer==self.remote);return self.access_check()
    def dispatch_check(self,context,intention):
        require(context==self.context and intention==self.intention);return self.access_check()
    read_check=dispatch_check
    def phase_check(self,context,kind,sends):
        require(context==self.context and type(kind) is int and kind in (H1,H2,CONFIRM_I,CONFIRM_R,DATA)
            and type(sends) is bool and sends==((kind%2==1)==self.local_initiator))
        return self.access_check()
    def key_check(self,public_key,context_digest):
        expected=self.context.initiator_noise_public_key if self.local_initiator else self.context.responder_noise_public_key
        require(public_key==expected and context_digest==self.context.digest)
        def check():
            self._observe();require(self._checked(self._key_check,public_key,context_digest) is True)
            self._observe();return True
        return self._run(check)
    def time(self):return self._run(self._observe)
    @contextmanager
    def _lease(self):
        from sage_lantern.storage_supervisor_socket import CaptureLease
        self.access_check()
        if self._held:self._uncertain=True;raise ForwardNativeOwnerError()
        mutex=self._refs[-1];require(mutex.acquire(blocking=False))
        lease=None;self._held=True
        try:
            self.access_check();lease=self.resource.capture_lease(self.consent)
            self._available();require(type(lease) is CaptureLease and lease.check() is True)
            self.access_check();yield
            self.access_check();require(lease.check() is True);self.access_check()
        except BaseException:
            self._uncertain=True;raise
        finally:
            try:
                if lease is not None:lease.close()
            except BaseException:
                self._uncertain=True;raise
            finally:self._held=False;mutex.release()
        self.access_check()
    def receiver(self):
        """Canonical composition over the borrowed client, without radio open."""
        self.access_check();require(self._composition is None)
        result=ForwardReceiver(self._refs[2],context=self.context,local_initiator=self.local_initiator,
            intention=self.intention,boot_id=self.intention.host_boot_id,endpoint=self.resource.probe.endpoint,
            signing_key=self._refs[4],owner_lease=self.owner_lease,access_check=self.access_check,
            storage_check=self.storage_check,peer_check=self.peer_check,dispatch_check=self.dispatch_check,
            read_check=self.read_check,key_check=self.key_check,clock=self.time)
        self.access_check();self._composition=self._composition_ref=result;return result
    def operation(self,noise_static_key,*,limits,prior_debt,sealed_record=None):
        """Prebind parent and both-role durable counters; creates no wire child.

        Limits/prior debt must come from the native owner's actual accounting.
        A source constructor does not verify physical debt observations.
        """
        from lantern_meshticulum.sage.forward_budget import ForwardBudget,Cost
        from lantern_meshticulum.sage.forward_operation import ForwardOperation
        self.access_check()
        if self._constructing:self._uncertain=True;raise ForwardNativeOwnerError()
        require(type(limits) is Cost and type(prior_debt) is Cost and self._composition is None)
        self._constructing=True
        try:
            receiver=self.receiver()
            slots=tuple((kind,(kind%2==1)==self.local_initiator) for kind in (H1,H2,CONFIRM_I,CONFIRM_R,DATA))
            budget=ForwardBudget(receiver.store,context=self.context,intention=self.intention,
                signing_key=self._refs[4],boot_id=self.intention.host_boot_id,limits=limits,prior_debt=prior_debt,
                slots=slots,owner_lease=self.owner_lease,access_check=self.access_check,storage_check=self.storage_check,
                native_check=lambda c,i,p:self.dispatch_check(c,i) if p is None else self.phase_check(c,p.kind,p.sends),
                clock=self.time)
            self._budget=budget;self._budget_refs=(receiver,receiver.store,receiver.journal,budget)
            budget.prepare()
            # Observe the actual shared signed quarantine while original read
            # authority is live; do not infer idle from a fresh Python object.
            require(receiver.capture.state()=='idle')
            session=receiver.create_session(noise_static_key)
            self._session=session
            require(session.counters is receiver.journal and receiver.journal.store is budget.store
                and receiver.journal._lease is budget._lease and receiver.journal.intention==budget.intention)
            operation=ForwardOperation(session,budget,sealed_record=sealed_record,
                receiver=None if self.local_initiator else receiver,native_check=self.phase_check)
            self._operation=operation
            self.access_check();self._available();return operation
        except BaseException:
            self._uncertain=True
            if self._composition is not None:self._composition.close()
            raise
        finally:self._constructing=False
    def prepare_stage(self,plan,*,open_child,native_check,retire_child,contain_child,terminal_check,payload=None):
        """Track cleanup ownership before the prepaid factory can open/ARM."""
        from lantern_meshticulum.sage.forward_operation import prepare_stage
        self.access_check();child=None
        def open_tracked(candidate):
            nonlocal child
            child=self._registry.begin(self,candidate,contain=contain_child,terminal=terminal_check)
            return open_child(candidate)
        try:
            runner=prepare_stage(self._operation,plan,open_child=open_tracked,native_check=native_check,
                retire_child=retire_child,payload=payload)
            self._registry.bind(child,runner);self._available();return runner
        except BaseException:
            if child is not None:self._registry.failed(child)
            self._uncertain=True;raise
    def retire_stage(self,runner):
        self.access_check();require(runner.operation is self._operation)
        self._registry.owns_runner(self,runner)
        runner.retire();result=self._registry.retired(runner)
        self._available();return result
    def fence(self):
        """Stop new work without touching resources still used by a producer."""
        self._closed=True
    def _close_components(self):
        operation_joined=self._operation is None or self._operation.close()
        budget_joined=self._budget is None or self._budget.close()
        joined=self._composition is None or self._composition.close()
        return (operation_joined and budget_joined and joined and not self._constructing
            and not self._held and not self._busy and not self._uncertain)
    def _registry_close(self,registry):
        """One-use same-process drain delegation after the registry idle barrier."""
        require(registry is self._registry and os.getpid()==self._owner[0] and self._closed
            and not self._held and not self._busy and not self._constructing)
        require(all(a is b for a,b in zip(self._native_refs,(self.resource,self.authority,self.native_context,
            self.context,self.intention,self.local_initiator,self.local,self.remote))))
        if self._composition is not None:require(self._composition is self._composition_ref)
        if self._budget is not None:
            receiver,store,journal,budget=self._budget_refs
            require(self._composition is receiver and self._budget is budget and receiver.store is store
                and receiver.journal is journal and journal.store is store and budget.store is store)
        if self._session is not None:require(self._composition.session is self._session
            and self._session.counters is self._budget_refs[2])
        require(registry.consume_close_permit(self) is True)
        return self._close_components()
    def close(self):
        self._closed=True
        owner=self._owner==(os.getpid(),threading.get_ident())
        return self._close_components() and owner
