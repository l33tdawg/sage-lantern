# SPDX-License-Identifier: Apache-2.0
"""Borrowed native owner observations for a single approved MPF2 exchange.

No radio factory, identity enrollment, private key loading or activation occurs.
The root caller must grant the four exact forward scopes over the full original
approval digest. Existing receive grants and unlocked state cannot supply them.
Activation additionally requires native child tracking/drain and key enrollment.
"""
from contextlib import contextmanager, nullcontext
import hashlib
import os
from pathlib import Path
import threading
import time
from types import MethodType
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
                 key_check,peer_key_check,boot_reader=host_boot,clock_reader=clock,exchange_key_check=None,
                 radio_native_check=None):
        from sage_lantern.mesh_daemon import HardwareRuntime
        from sage_lantern.mesh_community import MeshActionAuthority
        from sage_lantern.mesh_runtime import MeshUserContext
        require(type(resource) is HardwareRuntime and type(authority) is MeshActionAuthority
            and type(native_context) is MeshUserContext and type(context) is ForwardContext
            and type(intention) is ReceiveIntention and type(local_initiator) is bool
            and all(callable(f) for f in (key_check,peer_key_check,boot_reader,clock_reader))
            and (exchange_key_check is None or callable(exchange_key_check)))
        self.resource,self.authority,self.native_context=resource,authority,native_context
        self.context,self.intention,self.local_initiator=context,intention,local_initiator
        self.local=context.initiator if local_initiator else context.responder
        self.remote=context.responder if local_initiator else context.initiator
        self._key_check,self._peer_key_check,self._boot,self._clock=key_check,peer_key_check,boot_reader,clock_reader
        self._exchange_key_check=exchange_key_check
        self._radio_native_check=radio_native_check;self._radio_read=None;self._radio_facts=None
        if radio_native_check is not None:
            from .forward_native_provider import ForwardNativeProvider, _RADIO_NATIVE_CHECK
            require(type(radio_native_check) is MethodType
                and type(radio_native_check.__self__) is ForwardNativeProvider
                and radio_native_check.__func__ is _RADIO_NATIVE_CHECK)
        self._observer_refs=(key_check,peer_key_check,boot_reader,clock_reader,exchange_key_check,radio_native_check)
        self._composition=None;self._composition_ref=None;self._constructing=False
        self._budget=self._session=self._operation=None;self._budget_refs=None;self._registry=None
        self._operation_observing=False;self._operation_refs=None;self._operation_methods=None
        self._journal_action_ref=self._journal_action_method=None
        self._journal_receiver_available=self._journal_receiver_method=None
        self._journal_receiver_refs=None
        self._cleanup_component_methods=();self._cleanup_joined=None
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
        self._lease_ref=self.owner_lease
        self.access_check()
        from lantern_meshticulum.sage.forward_children import attach
        self._registry=attach(self)
        self.access_check()

    def __repr__(self):return '<ForwardNativeOwner>'
    def _available(self):
        require(not self._closed and not self._uncertain and self._owner==(os.getpid(),threading.get_ident()))
        observers_bound=type(self._observer_refs) is tuple and len(self._observer_refs)==6 and all(a is b for a,b in zip(self._observer_refs,
            (self._key_check,self._peer_key_check,self._boot,self._clock,self._exchange_key_check,self._radio_native_check)))
        if not observers_bound or self.owner_lease is not self._lease_ref:
            self._uncertain=True;raise ForwardNativeOwnerError()
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
            operation,session,receiver,budget,authorize,clock,native,action=self._operation_refs
            require(self._operation is operation and self._session is session and self._composition is receiver
                and self._budget is budget and session.authorize is authorize and session.clock is clock
                and operation._native is native and operation._operation_action is action)
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
    def _observe(self,*,live=True,keys=True,storage=True):
        h=self.resource;self._available()
        require(self._checked(h.configured) is True and self._checked(h.active) is True
            and self._checked(h.consent)==self.consent
            and hashlib.sha256(_canonical(h.config)).hexdigest()==self.config_revision
            and self._checked(h.supervisor.resource_lifecycle_check) is True
            and self._checked(h.supervisor.verified_epoch)==self.storage_epoch
            and (not storage or self._checked(h.probe.snapshot).epoch==self.storage_epoch)
            and self._checked(h._epoch)==self.native_context.epoch
            and self._checked(self._boot)==self.intention.host_boot_id)
        # Epoch/unlock is only a lifecycle fence. The live authority record owns
        # the original authenticated read and full-exchange permissions.
        for scope in SCOPES:
            require(self._checked(self.authority.authorized,self.native_context,scope,None) is True)
        require(self.consent.messaging_enabled is True and self.consent.dispatch_enabled is True)
        peers=h.config['peers']
        require(any(p['chain_id']==self.remote.chain_id and p['agent_id']==self.remote.public_key.hex() for p in peers))
        if keys:
            local_key=self.context.initiator_noise_public_key if self.local_initiator else self.context.responder_noise_public_key
            remote_key=self.context.responder_noise_public_key if self.local_initiator else self.context.initiator_noise_public_key
            if self._exchange_key_check is None:
                require(self._checked(self._key_check,local_key,self.context.digest) is True)
                require(self._checked(self._peer_key_check,self.remote,remote_key,self.context.digest) is True)
            else:
                require(self._checked(self._exchange_key_check,local_key,self.remote,remote_key,self.context.digest) is True)
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
    def storage_check(self):
        # A fresh native/Vault fence supplies no key or dispatch permission.
        # Access, dispatch, read, phase and crypto checks still reread both
        # signed purpose/enrollment records at their own action boundary.
        return self._run(lambda:(self._observe(keys=False),True)[1])
    def peer_check(self,peer):
        require(peer==self.remote);return self.access_check()
    def dispatch_check(self,context,intention):
        require(context==self.context and intention==self.intention);return self.access_check()
    read_check=dispatch_check
    def phase_check(self,context,kind,sends):
        require(context==self.context and type(kind) is int and kind in (H1,H2,CONFIRM_I,CONFIRM_R,DATA)
            and type(sends) is bool and sends==((kind%2==1)==self.local_initiator))
        return self.access_check()
    def observe_radio_adoption(self,adoption):
        """One read-only signed-adoption action, independent of CaptureLease.

        Key authorization brackets the complete read. Every internal SDK fence
        freshly observes native permission, Vault and held accounting instead;
        this busy interval cannot dispatch, encrypt or open another child.
        """
        return self._observe_radio(adoption)

    def observe_radio_facts(self,factory,mode):
        """A finite factual read, never a transport or private-key permission."""
        from .forward_radio import ForwardRadioFactory, _RadioFactsMode
        require(type(factory) is ForwardRadioFactory and type(mode) is _RadioFactsMode
            and factory.owner is self and self._radio_native_check is not None)
        adoption=factory.adoption
        if adoption._busy:
            adoption._uncertain=self._uncertain=True;raise ForwardNativeOwnerError()
        adoption._busy=True
        try:return self._observe_radio(adoption,factory,mode)
        except BaseException:
            adoption._uncertain=True;raise
        finally:adoption._busy=False

    def _observe_radio(self,adoption,factory=None,mode=None):
        from .radio_native_adoption import NativeRadioAdoption, _ADOPTION_READ_BODY, _ADOPTION_GUARD_TAIL
        from .forward_native_provider import ForwardNativeProvider, _RADIO_NATIVE_CHECK
        from .forward_radio import ForwardRadioFactory, ForwardRadioError, _RadioFactsMode
        from . import forward_radio as radio
        modes=radio._RADIO_FACTS_MODES
        require(type(adoption) is NativeRadioAdoption)
        require(factory is None and mode is None or type(factory) is ForwardRadioFactory
            and type(mode) is _RadioFactsMode and any(mode is m for m in modes)
            and factory.owner is self and factory.adoption is adoption)
        callback=self._radio_native_check
        require(type(callback) is MethodType and type(callback.__self__) is ForwardNativeProvider
            and callback.__func__ is _RADIO_NATIVE_CHECK)
        provider=callback.__self__;child=adoption.child;epoch=self.storage_epoch
        methods=((self,'observe_radio_adoption',_RADIO_ADOPTION_OBSERVER),
            (self,'observe_radio_facts',_RADIO_FACTS_OBSERVER),(self,'_observe_radio',_RADIO_OBSERVER),
            (provider,'_radio_native_check',_RADIO_NATIVE_CHECK),
            (adoption,'_read_record',_ADOPTION_READ_BODY),(adoption,'_guard_tail',_ADOPTION_GUARD_TAIL)
            )+tuple((self,name,original) for name,original in _RADIO_OWNER_METHODS)
        refs=(adoption,child,adoption.store,adoption._child,callback,epoch)
        read_action=adoption._read_action
        value=_canonical(child.to_dict());run=object()
        facts=None if factory is None else (factory,mode,run)
        original_record=(adoption._highest,adoption._record)
        initial_error=ForwardRadioError() if mode is _RadioFactsMode.LIVE else None
        def bindings(*,observed=True):
            require(radio._RADIO_FACTS_MODES is modes and type(modes) is tuple and len(modes)==3
                and all(a is b for a,b in zip(modes,(_RadioFactsMode.DEVICE,_RadioFactsMode.LIVE,_RadioFactsMode.NATIVE))))
            self._available();adoption._bound()
            require(self._busy and self._radio_read is run and adoption._read_run is run
                and self._radio_facts is facts
                and adoption._busy and not adoption._closed and not adoption._uncertain
                and adoption._read_action is adoption._read_action_ref is read_action
                and type(read_action) is MethodType and read_action.__self__ is self
                and read_action.__func__ is _RADIO_ADOPTION_OBSERVER
                and adoption.owner is self and self._radio_native_check is callback
                and all(a is b for a,b in zip(refs,(adoption,adoption.child,adoption.store,
                    adoption._child,self._radio_native_check,self.storage_epoch)))
                and _canonical(child.to_dict())==value and type(child.kind) is int
                and type(child.sends) is bool and child.sends==((child.kind%2==1)==self.local_initiator)
                and child.kind in (H1,H2,CONFIRM_I,CONFIRM_R,DATA)
                and adoption.store is self._composition.store
                and (adoption._highest,adoption._record)==original_record
                and type(provider)._radio_native_check is _RADIO_NATIVE_CHECK
                and '_radio_native_check' not in provider.__dict__)
            require(all(name not in obj.__dict__ and getattr(type(obj),name) is original
                        for obj,name,original in methods))
            if factory is not None:
                factory._bound()
                require(factory.owner is self and factory.adoption is adoption
                    and factory.observer.adoption is adoption and factory.observer._factory is factory
                    and adoption._adopted is True and self.resource._forward_radio is adoption
                    and (factory._native_observing and adoption._child.observing
                        and adoption._child.observe_thread==threading.get_ident() if observed else
                        not factory._native_observing and not adoption._child.observing))
            children=[c for c in self._registry._children if c.owner is self and c.plan is child
                and not c.retired and not c.unknown]
            require(len(children)==1)
            tracked=children[0];self._registry._child(tracked)
            require(adoption._child is tracked if adoption._adopted else
                adoption._child is None and tracked.opening and tracked.open_thread==threading.get_ident())
            if tracked.opening:
                require(any(a.plan is child and not a.bound for a in self._budget._permits.values()))
            else:self._registry._paid_bound(tracked)
        def guard():
            bindings()
            require(callback(self,adoption,child,epoch) is True);bindings()
            _ADOPTION_GUARD_TAIL(adoption);bindings()
            return True
        def full():
            bindings();self._observe();bindings();_ADOPTION_GUARD_TAIL(adoption);bindings()
        pending=original_record
        def row(before,after):
            nonlocal pending
            before();adoption._assets();bindings()
            revision,record=_ADOPTION_READ_BODY(adoption,guard,epoch)
            bindings();adoption._assets();bindings();after()
            require(revision>=pending[0] and (revision!=pending[0] or record==pending[1]))
            pending=revision,record
        def read():
            require(self._radio_read is None and adoption._read_run is None and self._radio_facts is None)
            self._radio_read=adoption._read_run=run
            self._radio_facts=facts
            try:
                observed=(nullcontext() if factory is None else factory._observed(
                    self.context,child,factory.transfer,initial_error=initial_error))
                with observed as checked:
                    if factory is None:row(full,full)
                    else:
                        try:row(full,guard)
                        except Exception:
                            if initial_error is None:raise
                            adoption._uncertain=True;raise initial_error from None
                        checked(factory.observer._device_facts)
                        if mode is _RadioFactsMode.NATIVE:
                            row(guard,guard);checked(factory.observer._device_facts)
                            # The original worker predicate is pure, but still
                            # inside the last full authorization bookend.
                            def finish():
                                checked(factory.observer._worker_current,factory.worker);full()
                            row(guard,finish)
                        else:row(guard,full)
                    bindings()
                bindings(observed=False);adoption._highest,adoption._record=pending
                return True
            finally:
                if self._radio_facts is facts:self._radio_facts=None
                else:self._uncertain=True
                if self._radio_read is run:self._radio_read=None
                else:self._uncertain=True
                if adoption._read_run is run:adoption._read_run=None
                else:adoption._uncertain=True
        return self._run(read)
    def key_check(self,public_key,context_digest):
        expected=self.context.initiator_noise_public_key if self.local_initiator else self.context.responder_noise_public_key
        require(public_key==expected and context_digest==self.context.digest)
        def check():
            self._observe();require(self._checked(self._key_check,public_key,context_digest) is True)
            self._observe();return True
        return self._run(check)
    def crypto_action_check(self,context,intention,remote,local_public_key,context_digest):
        """One fresh full native authorization for this exact crypto action.

        The complete local/remote key observer runs on every invocation, fenced
        by fresh native/Vault/authority/lifetime observations on both sides.
        Its result is never reused by another action or transport boundary.
        """
        def bindings():
            expected=self.context.initiator_noise_public_key if self.local_initiator else self.context.responder_noise_public_key
            require(context is self.context and intention is self.intention and remote==self.remote
                and type(remote) is type(self.remote) and type(local_public_key) is bytes
                and local_public_key==expected and type(context_digest) is bytes
                and context_digest==self.context.digest)
        def check():
            bindings();self._observe(keys=False)
            remote_key=self.context.responder_noise_public_key if self.local_initiator else self.context.initiator_noise_public_key
            if self._exchange_key_check is None:
                require(self._checked(self._key_check,local_public_key,context_digest) is True)
                require(self._checked(self._peer_key_check,remote,remote_key,context_digest) is True)
            else:
                require(self._checked(self._exchange_key_check,local_public_key,remote,remote_key,context_digest) is True)
            self._observe(keys=False);bindings();return True
        return self._run(check)
    def budget_action_check(self,context,intention,plan):
        """Fresh complete action approval at each existing budget boundary."""
        from .forward_budget import ChildPlan
        original=None if plan is None else _canonical(plan.to_dict()) if type(plan) is ChildPlan else None
        def bindings():
            require(context is self.context and intention is self.intention)
            if plan is not None:
                require(type(plan) is ChildPlan and type(plan.kind) is int
                    and type(plan.slot) is int and type(plan.sends) is bool
                    and plan.kind in (H1,H2,CONFIRM_I,CONFIRM_R,DATA)
                    and plan.slot==plan.kind-1 and plan.sends==((plan.kind%2==1)==self.local_initiator)
                    and plan.host_boot_id==self.intention.host_boot_id
                    and type(plan.application_expires_epoch_us) is int
                    and type(plan.application_deadline_monotonic_us) is int
                    and plan.application_expires_epoch_us==self.context.expires_epoch_us
                    and plan.application_deadline_monotonic_us<=self.intention.application_deadline_monotonic_us
                    and _canonical(plan.to_dict())==original)
        def check():
            bindings();self._observe(keys=False)
            local_key=self.context.initiator_noise_public_key if self.local_initiator else self.context.responder_noise_public_key
            remote_key=self.context.responder_noise_public_key if self.local_initiator else self.context.initiator_noise_public_key
            if self._exchange_key_check is None:
                require(self._checked(self._key_check,local_key,self.context.digest) is True)
                require(self._checked(self._peer_key_check,self.remote,remote_key,self.context.digest) is True)
            else:
                require(self._checked(self._exchange_key_check,local_key,self.remote,remote_key,self.context.digest) is True)
            self._observe(keys=False);bindings();return True
        return self._run(check)
    def operation_action_check(self,context,intention,kind,sends,session,authorize):
        """Combine phase approval with the original full crypto authorizer.

        Each existing operation boundary still calls this independently. The
        receiver observes both current key records; the final native check
        covers callback-side drift without reading or retaining those keys.
        """
        entered=False
        def bindings():
            self._available()
            from .forward_operation import ForwardOperation
            operation,original,receiver,budget,callback,clock,native,action=self._operation_refs
            require(type(operation) is ForwardOperation and operation is self._operation
                and operation._action_observing and operation._busy
                and context is self.context and intention is self.intention
                and type(kind) is int and kind in (H1,H2,CONFIRM_I,CONFIRM_R,DATA)
                and type(sends) is bool and sends==((kind%2==1)==self.local_initiator)
                and session is original is self._session and authorize is callback is session.authorize
                and type(receiver) is ForwardReceiver and receiver is self._composition
                and receiver.session is session and receiver.context is context and receiver.intention is intention
                and receiver.local_initiator is self.local_initiator and receiver.journal is session.counters
                and receiver.store is budget.store and operation.budget is budget
                and getattr(authorize,'__self__',None) is receiver
                and getattr(authorize,'__func__',None) is self._operation_methods[0]
                and type(receiver)._authorize_crypto is self._operation_methods[0]
                and type(self).operation_action_check is self._operation_methods[1]
                and '_authorize_crypto' not in receiver.__dict__ and 'operation_action_check' not in self.__dict__)
            operation._action_bound()
        try:
            if self._operation_observing or self._busy:
                self._uncertain=True;raise ForwardNativeOwnerError()
            bindings();self._operation_observing=entered=True
            # This original callback enters crypto_action_check._run itself;
            # an outer native _run would be incorrect self-reentry.
            require(authorize() is True);bindings()
            self._busy=True
            try:self._observe(keys=False);bindings()
            finally:self._busy=False
            return True
        except BaseException as error:
            self._uncertain=True
            if not isinstance(error,Exception):raise
            raise ForwardNativeOwnerError() from None
        finally:
            if entered:self._operation_observing=False
    def time(self):return self._run(lambda:self._observe(keys=False))
    def journal_action_check(self,journal,context,intention,remote,local_initiator):
        """Full fresh purpose/enrollment authorization for one journal guard.

        Initial claim runs during session construction, before the session and
        operation are installed. The original journal/store/budget already exist.
        Read-only recovery never enters this callback.
        """
        from .forward_receive_journal import ForwardReceiveJournal
        from .forward_budget import ForwardBudget
        def references():
            self._available()
            require(self._budget_refs is not None)
            receiver,store,original,budget=self._budget_refs
            require(type(journal) is ForwardReceiveJournal and journal is original is receiver.journal
                and receiver is self._composition_ref is self._composition
                and type(receiver) is ForwardReceiver and type(budget) is ForwardBudget
                and self._budget is budget and journal.store is receiver.store is budget.store is store
                and journal._lease is receiver._lease is budget._lease is self.owner_lease
                and context is journal.context is receiver.context is budget.context is self.context
                and intention is journal.intention is receiver.intention is budget.intention is self.intention
                and remote is journal.remote is receiver.remote is self.remote
                and type(local_initiator) is bool
                and local_initiator is journal.local_initiator is receiver.local_initiator is self.local_initiator
                and journal._busy and journal._guarding and journal._fresh is True
                and journal._journal_action is receiver._journal_action is self._journal_action_ref
                and getattr(self._journal_action_ref,'__self__',None) is self
                and getattr(self._journal_action_ref,'__func__',None) is self._journal_action_method
                and type(self).journal_action_check is self._journal_action_method
                and 'journal_action_check' not in self.__dict__
                and getattr(self._journal_receiver_available,'__self__',None) is receiver
                and getattr(self._journal_receiver_available,'__func__',None) is self._journal_receiver_method
                and type(receiver)._available is self._journal_receiver_method
                and '_available' not in receiver.__dict__)
            require(not receiver._closed and not receiver._uncertain
                and receiver._owner==(os.getpid(),threading.get_ident())
                and receiver._workflow_access is receiver._workflow_access_ref
                and receiver._crypto_action is receiver._crypto_action_ref
                and receiver._journal_action is receiver._journal_action_ref
                and all(a is b for a,b in zip(self._journal_receiver_refs,
                    (receiver.client,receiver.client._client,receiver.client._identity,receiver.probe,
                     receiver._key,receiver._lease,receiver._access,receiver._storage,receiver._peer,
                     receiver._dispatch,receiver._read,receiver._purpose,receiver._clock,
                     receiver._workflow_access,receiver._crypto_action)))
                and receiver.client is store.client is receiver.probe.client
                and receiver.client._client is receiver.probe._transport
                and receiver.client._identity is receiver.probe._identity
                and receiver.client._identity._signing_key is receiver._key is self._refs[4]
                and receiver.client._client.is_closed is False)
            journal._available()
        def bindings():
            references();self._checked(self._journal_receiver_available);references()
        def check():
            bindings();self._observe(keys=False)
            local_key=self.context.initiator_noise_public_key if self.local_initiator else self.context.responder_noise_public_key
            remote_key=self.context.responder_noise_public_key if self.local_initiator else self.context.initiator_noise_public_key
            if self._exchange_key_check is None:
                require(self._checked(self._key_check,local_key,self.context.digest) is True)
                require(self._checked(self._peer_key_check,remote,remote_key,self.context.digest) is True)
            else:
                require(self._checked(self._exchange_key_check,local_key,remote,remote_key,self.context.digest) is True)
            self._observe(keys=False);bindings();return True
        return self._run(check)
    def workflow_access_check(self):
        """Fresh original native access inside one held metadata operation.

        WorkflowStore owns fresh HTTP epochs on both sides of the SDK call.
        This callback supplies no key or wire permission and cannot be used
        outside the original native lease. Action/crypto observers stay full.
        """
        require(self._held and self._refs[-1].locked())
        return self._run(lambda:(self._observe(keys=False,storage=False),True)[1])
    @contextmanager
    def _lease(self):
        from sage_lantern.storage_supervisor_socket import CaptureLease
        # A held lease supplies no crypto or wire permission. Keep every fresh
        # native/lifetime observation; action guards own signed-key validation.
        self.storage_check()
        if self._held:self._uncertain=True;raise ForwardNativeOwnerError()
        mutex=self._refs[-1];require(mutex.acquire(blocking=False))
        lease=None;self._held=True
        try:
            self.storage_check();lease=self.resource.capture_lease(self.consent)
            self._available();require(type(lease) is CaptureLease and lease.check() is True)
            self.storage_check();yield
            self.storage_check();require(lease.check() is True);self.storage_check()
        except BaseException:
            self._uncertain=True;raise
        finally:
            try:
                if lease is not None:lease.close()
            except BaseException:
                self._uncertain=True;raise
            finally:self._held=False;mutex.release()
        # BinaryCapture can publish after lease exit without another full
        # action guard. Reobserve signed keys after actual close/release.
        self.access_check()
    def receiver(self):
        """Canonical composition over the borrowed client, without radio open."""
        self.access_check();require(self._composition is None)
        self._journal_action_ref=self.journal_action_check
        self._journal_action_method=type(self).journal_action_check
        result=ForwardReceiver(self._refs[2],context=self.context,local_initiator=self.local_initiator,
            intention=self.intention,boot_id=self.intention.host_boot_id,endpoint=self.resource.probe.endpoint,
            signing_key=self._refs[4],owner_lease=self.owner_lease,access_check=self.access_check,
            storage_check=self.storage_check,peer_check=self.peer_check,dispatch_check=self.dispatch_check,
            read_check=self.read_check,key_check=self.key_check,clock=self.time,
            workflow_access_check=self.workflow_access_check,crypto_action_check=self.crypto_action_check,
            journal_action_check=self._journal_action_ref)
        self._journal_receiver_available=result._available
        self._journal_receiver_method=type(result)._available
        self._journal_receiver_refs=(result.client,result.client._client,result.client._identity,result.probe,
            result._key,result._lease,result._access,result._storage,result._peer,result._dispatch,
            result._read,result._purpose,result._clock,result._workflow_access,result._crypto_action)
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
                clock=self.time,workflow_access_check=self.workflow_access_check,
                budget_action_check=self.budget_action_check)
            self._budget=budget;self._budget_refs=(receiver,receiver.store,receiver.journal,budget)
            budget.prepare()
            # Observe the actual shared signed quarantine while original read
            # authority is live; do not infer idle from a fresh Python object.
            require(receiver.capture.state()=='idle')
            session=receiver.create_session(noise_static_key)
            self._session=session
            require(session.counters is receiver.journal and receiver.journal.store is budget.store
                and receiver.journal._lease is budget._lease and receiver.journal.intention==budget.intention)
            native=self.phase_check;action=self.operation_action_check
            operation=ForwardOperation(session,budget,sealed_record=sealed_record,
                receiver=None if self.local_initiator else receiver,native_check=native,operation_action_check=action)
            self._operation_refs=(operation,session,receiver,budget,session.authorize,session.clock,native,action)
            self._operation_methods=(type(receiver)._authorize_crypto,type(self).operation_action_check)
            self._operation=operation
            self._cleanup_component_methods=tuple((obj,name,getattr(type(obj),name))
                for obj,names in ((operation,('close','acknowledge_cancelled')),
                    (operation.session,('close',)),(self._budget,('close',)),
                    (receiver,('close',)),(receiver.journal,('close',)),(receiver.capture,('close',)))
                for name in names)
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
    def _close_components(self,pending=None):
        operation_joined=self._operation is None or self._operation.close()
        budget_joined=self._budget is None or self._budget.close()
        joined=self._composition is None or self._composition.close()
        self._cleanup_joined=(operation_joined,budget_joined,joined)
        idle=(not self._constructing
            and not self._held and not self._busy and not self._operation_observing and not self._uncertain)
        if pending is not None:
            require(self._registry.cancellation_pending(self) is pending)
            return pending if idle and budget_joined and joined else False
        return operation_joined and budget_joined and joined and idle
    def _cleanup_bound(self):
        require(os.getpid()==self._owner[0])
        require(all(a is b for a,b in zip(self._native_refs,(self.resource,self.authority,self.native_context,
            self.context,self.intention,self.local_initiator,self.local,self.remote))))
        if self._composition is not None:require(self._composition is self._composition_ref)
        if self._budget is not None:
            receiver,store,journal,budget=self._budget_refs
            require(self._composition is receiver and self._budget is budget and receiver.store is store
                and receiver.journal is journal and journal.store is store and budget.store is store)
        if self._session is not None:require(self._composition.session is self._session
            and self._session.counters is self._budget_refs[2])
        if self._operation is not None:
            operation,session,receiver,budget,authorize,clock,native,action=self._operation_refs
            require(self._operation is operation and self._session is session and self._composition is receiver
                and self._budget is budget and operation.session is session and operation.budget is budget
                and session.authorize is authorize and session.clock is clock
                and operation._native is native and operation._operation_action is action)
        for obj,name,method in self._cleanup_component_methods:
            require(name not in obj.__dict__ and getattr(type(obj),name) is method)
    def _registry_close(self,registry):
        """Fence before containment; a known active cancellation stays pending."""
        require(registry is self._registry and self._closed
            and not self._held and not self._busy and not self._operation_observing and not self._constructing)
        self._cleanup_bound()
        require(registry.consume_close_permit(self) is True)
        pending=(registry.cancellation_pending(self)
                 if self._operation is not None and self._operation.active is not None else None)
        return self._close_components(pending)
    def _registry_finish_close(self,registry):
        require(registry is self._registry and self._closed)
        self._cleanup_bound()
        if self._operation is not None and self._operation.active is not None:
            runner=self._operation.active
            require(self._operation.acknowledge_cancelled(registry.cleanup_permit(self,self._operation),runner))
        joined=self._close_components()
        require(joined is True)
        registry.endpoint_joined(self)
        bundle=registry._endpoint(self)
        if bundle is not None:
            provider=bundle.refs[1]
            provider_joined=provider._registry_finish_close(registry.cleanup_permit(self,provider))
            joined=joined and provider_joined
        return joined
    def close(self):
        self._closed=True
        owner=self._owner==(os.getpid(),threading.get_ident())
        return self._close_components() and owner

_RADIO_ADOPTION_OBSERVER=ForwardNativeOwner.observe_radio_adoption
_RADIO_FACTS_OBSERVER=ForwardNativeOwner.observe_radio_facts
_RADIO_OBSERVER=ForwardNativeOwner._observe_radio
_RADIO_OWNER_METHODS=tuple((name,getattr(ForwardNativeOwner,name))
    for name in ('_run','_available','_observe','_checked'))
