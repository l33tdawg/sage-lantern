# SPDX-License-Identifier: Apache-2.0
"""Native A ownership of one opaque C/B exchange, with ten prepaid children.

The relay has its own identity and approval, not either endpoint's Noise key,
session or private inbox. Factory observations require a concrete held native
implementation; a description, Python subclass or terminal DTO is not physical
qualification. Existing endpoint radio/IP adopters are deliberately not widened.
All outcomes retain complete child maxima and unknown usage, without refunds.
"""
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import os
import sqlite3
import threading
import time
from types import MethodType

from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.services import ServiceTransfer
from meshticulum.contracts.identity import PeerID
from meshticulum.privacy.forward_relay import (
    OpaqueForwardRelay, RelayDescription, RelayHop, RelayStage, SEQUENCE, describe_stage,
)
from .forward_budget import ForwardBudget, ChildPlan, Cost
from .forward_native_owner import SCOPES, host_boot, clock
from .forward_native_provider import NativeAccountingSource, NativeAccountingObservation
from .bound_storage_pair import BoundStoragePair, BoundStoragePairError
from .forward_operation import describe_child
from .forward_receive_journal import ReceiveIntention
from .message_contacts import RecipientID
from .message_storage import MessageStorageProbe
from .mesh_inbox import _canonical
from .workflow_store import WorkflowStore

RELAY_SCOPE = 'meshticulum_forward_relay'
RELAY_SCOPES = SCOPES + (RELAY_SCOPE,)
DOMAIN = b'sage.lantern.native-forward-relay-approval.v1\0'


class ForwardNativeRelayError(RuntimeError):
    def __init__(self): super().__init__('forward_native_relay_unavailable')


def require(value):
    if not value: raise ForwardNativeRelayError()


def digest(value):
    require(type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value))
    return value


def counter(value, maximum):
    require(type(value) is int and 0 <= value <= maximum)
    return value


def description_value(description):
    require(type(description) is RelayDescription)
    return dict(context=description.context.digest.hex(), relay=asdict(description.relay_peer),
                deadlines=asdict(description.deadlines), max_packet_bytes=description.max_packet_bytes)


def relay_approval_binding(description, intention, limits, accounting, *,
                           config_revision, broker_id, storage_epoch):
    require(type(intention) is ReceiveIntention and type(limits) is Cost
            and type(accounting) is NativeAccountingObservation
            and accounting.retained_debt.within(limits))
    value = dict(description=description_value(description), intention=intention.to_dict(),
        limits=asdict(limits), accounting=accounting.binding(), config_revision=digest(config_revision),
        broker_id=broker_id, storage_epoch=storage_epoch)
    return hashlib.sha256(DOMAIN + _canonical(value)).hexdigest()


@dataclass(frozen=True)
class RelayPrepared:
    """A fresh held-factory observation; its fields alone grant no permission."""
    plan: ChildPlan
    hop: RelayHop
    evidence_sha256: str
    host_boot_id: str
    observed_epoch_us: int
    observed_monotonic_us: int

    def binding(self):
        require(type(self.plan) is ChildPlan and type(self.hop) is RelayHop)
        return self.plan, self.hop, digest(self.evidence_sha256), self.host_boot_id


@dataclass(frozen=True)
class RelayTerminal:
    prepared: RelayPrepared
    journal_snapshot: dict
    journal_sha256: str
    retirement_evidence_sha256: str
    resources_closed: bool
    pending_unknown: bool
    radio_disarmed: bool | None


class NativeRelayFactory(ABC):
    """Mandatory held native adapter, independently qualified outside this API.

prepared/current must verify protected original grant, profile and enforced
maxima, source/FD ownership and current native admission. open must retain every
partial handle before returning or raising. retire performs the actual bounded
ACK/disarm/close protocol; contain may only stop owned work after fencing.
terminal freshly observes retained native retirement evidence and journals,
never substitutes flow completion or a caller boolean. None of these methods
may mint a grant, renew a clock, reconstruct an endpoint or refund a reservation.
"""
    @abstractmethod
    def prepared(self, owner, plan, hop): pass
    @abstractmethod
    def open(self, owner, child): pass
    @abstractmethod
    def current(self, owner, child): pass
    @abstractmethod
    def retire(self, owner, child, deadline): pass
    @abstractmethod
    def contain(self, owner, child, deadline): pass
    @abstractmethod
    def terminal(self, owner, child): pass


_FACTORY_METHODS = ('prepared', 'open', 'current', 'retire', 'contain', 'terminal')


class RelayChild:
    """Owner-retained per-hop state; copying it cannot recover admission."""
    def __init__(self, owner, plan, hop, factory):
        self.owner, self.plan, self.hop, self.factory = owner, plan, hop, factory
        self._refs = owner, plan, hop, factory
        self._methods = tuple(getattr(type(factory), name) for name in _FACTORY_METHODS)
        self.prepared = self.admission = self.transfer = self.registration = None
        self._transport_refs = self._plan_bytes = self._journal = None
        self._executing = self.retired = False
        self._terminal_value = None

    def bound(self):
        require(all(a is b for a, b in zip(self._refs, (self.owner, self.plan, self.hop, self.factory)))
                and self._methods == tuple(getattr(type(self.factory), name) for name in _FACTORY_METHODS)
                and not any(name in self.factory.__dict__ for name in _FACTORY_METHODS))
        if self._transport_refs is not None:
            require(all(a is b for a, b in zip(self._transport_refs, self.transport_refs()))
                    and _canonical(self.transfer.authority.plan) == self._plan_bytes)

    def transport_refs(self):
        t = self.transfer
        return (t, t.flow, t.authority, t.flow.worker, t.flow.cipher, t.authority.grant,
                t.flow.journal if type(t.flow) is BenchFlow else t.flow.worker.journal, t.flow.cipher.store)


class ForwardNativeRelay:
    def __init__(self, resource, authority, native_context, *, description, intention,
                 limits, accounting, boot_reader=host_boot, clock_reader=clock,
                 storage_pair_factory=BoundStoragePair):
        from sage_lantern.mesh_daemon import HardwareRuntime
        from sage_lantern.mesh_community import MeshActionAuthority
        from sage_lantern.mesh_runtime import MeshUserContext
        require(type(resource) is HardwareRuntime and type(authority) is MeshActionAuthority
                and type(native_context) is MeshUserContext and type(description) is RelayDescription
                and type(intention) is ReceiveIntention and type(limits) is Cost
                and isinstance(accounting, NativeAccountingSource)
                and callable(boot_reader) and callable(clock_reader)
                and (storage_pair_factory is None or storage_pair_factory is BoundStoragePair))
        self.resource, self.authority, self.native_context = resource, authority, native_context
        self.description, self.context, self.intention = description, description.context, intention
        self.limits, self.accounting = limits, accounting
        self._boot, self._clock = boot_reader, clock_reader
        self._owner = os.getpid(), threading.get_ident()
        self._busy = self._held = self._observing = self._closed = self._uncertain = False
        self._source_closed = self._drain_pending = False
        self._cleanup_thread = None
        self._core_closed = False
        self._registry = self._budget = self._relay = None
        self._active = None
        self._budget_stage = None
        self._budget_observing = False
        self._children = []
        self._position = 0
        self._last_time = self._accounting_binding = self._approval = None
        self._storage_pair_factory = storage_pair_factory
        self._storage_pair = self._storage_pair_ref = None
        self._storage_pair_check = self._storage_pair_check_ref = None
        self._storage_pair_matches = None
        self._storage_pair_delegates = None
        self._refs = (resource, authority, native_context, description, intention, limits,
                      accounting, boot_reader, clock_reader, storage_pair_factory)
        self._native_refs = (resource.supervisor, resource.probe, resource.probe.client,
                            resource.identity, resource.identity._signing_key, resource._mutex)
        self._source_methods = (type(accounting).observe, type(accounting).close,
                                getattr(type(accounting), 'close_from_registry', None))
        self._source_close = accounting, self._source_methods[1]
        try:
            require(self._source_methods[2] is None or callable(self._source_methods[2]))
            self.local = PeerID(resource.actor.chain_id, resource.identity.agent_id)
            self.config_revision = hashlib.sha256(_canonical(resource.config)).hexdigest()
            self.consent, self.broker_id = resource.consent(), resource.supervisor.broker_id
            self.storage_epoch = resource.probe.snapshot().epoch
            self._facts = _canonical(dict(description=description_value(description), intention=intention.to_dict(),
                limits=asdict(limits), local=asdict(self.local), config=self.config_revision,
                broker=self.broker_id, storage=self.storage_epoch))
            self._store = WorkflowStore(resource.probe.client, storage=MessageStorageProbe(resource.probe.client,
                actor=RecipientID(self.local.chain_id, self.local.agent_id), endpoint=resource.probe.endpoint))
            if storage_pair_factory is not None:
                self._storage_pair = self._storage_pair_ref = storage_pair_factory(resource.probe, self._store)
                self._storage_pair_check = self._storage_pair_check_ref = self._storage_pair.observe
                self._storage_pair_matches = self._storage_pair.matches
                self._storage_pair_delegates = (self._storage_pair._BoundStoragePair__matches,
                                               self._storage_pair._BoundStoragePair__observe)
            self._store_refs = (self._store, self._store.storage, self._store.client,
                               self._storage_pair_delegates, self._storage_pair_matches)
            self.owner_lease = self._lease
            self._lease_ref = self.owner_lease
            self._budget_action = self.budget_action_check
            self._budget_callbacks = (self.access_check, self.access_check, self._budget_check, self.time)
            self._budget_methods = tuple((target, name, getattr(type(target), name)) for target, name in
                ((self, 'budget_action_check'), (self, 'access_check'), (self, '_budget_check'),
                 (self, 'time'), (self, '_validate_pair'), (self._store, 'get'), (self._store, 'put'),
                 (self._store.storage, 'snapshot')))
            self._budget_plan_methods = ChildPlan.__post_init__, Cost.__post_init__
            require(self.local == description.relay_peer and resource.actor.agent_id == self.local.agent_id
                and self.local.agent_id not in (self.context.initiator.public_key.hex(), self.context.responder.public_key.hex())
                and native_context.actor == resource.actor
                and intention.authority_digest == self.context.binding_digest.hex()
                and intention.application_expires_epoch_us == self.context.expires_epoch_us
                and description.deadlines.application_deadline_monotonic
                    <= intention.application_deadline_monotonic_us / 1000000)
            self.access_check()
            self._budget = ForwardBudget(self._store, context=self.context, intention=intention,
                signing_key=self._native_refs[4], boot_id=intention.host_boot_id, limits=limits,
                prior_debt=self._retained_debt, slots=tuple((kind, sends) for kind in SEQUENCE for sends in (False, True)),
                owner_lease=self.owner_lease, access_check=self._budget_callbacks[0],
                storage_check=self._budget_callbacks[1], native_check=self._budget_callbacks[2],
                clock=self._budget_callbacks[3], budget_action_check=self._budget_action)
            self._budget_refs = self._budget, self._budget.binding, _canonical(self._budget.binding)
            self._relay = OpaqueForwardRelay(description, clock=self.time)
            self._relay_ref = self._relay
            self._core_token = self._relay.prepare_close_delegation()
            self._core_token_ref = self._core_token
            self._core_methods = tuple(getattr(OpaqueForwardRelay, name) for name in
                ('close', 'snapshot', 'prepare_close_delegation', 'close_delegated'))
            from .forward_children import attach
            self._registry = attach(self)
            self.access_check()
        except BaseException as error:
            self._uncertain = True
            self.close()
            if isinstance(error, BoundStoragePairError): raise ForwardNativeRelayError() from None
            raise

    def __repr__(self): return '<ForwardNativeRelay>'

    def _available(self, *, cleanup=False):
        current = os.getpid(), threading.get_ident()
        delegated = (cleanup and self._cleanup_thread == current and self._drain_pending
            and self._registry is not None and self._registry._closing and self._registry._busy
            and self._registry._drain_thread == current[1]
            and self._registry._owners.get(id(self)) is self and current[0] == self._owner[0])
        require((self._owner == current or delegated)
                and (cleanup or not self._closed and not self._uncertain and not self._source_closed))
        h = self.resource
        require(all(a is b for a, b in zip(self._refs, (h, self.authority, self.native_context,
            self.description, self.intention, self.limits, self.accounting, self._boot, self._clock,
            self._storage_pair_factory)))
            and all(a is b for a, b in zip(self._native_refs, (h.supervisor, h.probe, h.probe.client,
                h.identity, h.identity._signing_key, h._mutex)))
            and self.context is self.description.context and self.owner_lease is self._lease_ref
            and all(a is b for a, b in zip(self._store_refs,
                (self._store, self._store.storage, self._store.client,
                 self._storage_pair_delegates, self._storage_pair_matches)))
            and self._store.client is h.probe.client
            and self._source_methods == (type(self.accounting).observe, type(self.accounting).close,
                                         getattr(type(self.accounting), 'close_from_registry', None))
            and not any(name in self.accounting.__dict__ for name in ('observe', 'close', 'close_from_registry')))
        require(self._storage_pair is self._storage_pair_ref
            and self._storage_pair_check is self._storage_pair_check_ref)
        if self._storage_pair_factory is None:
            require(self._storage_pair is None and self._storage_pair_check is None
                and self._storage_pair_delegates is None and self._storage_pair_matches is None)
        else:
            require(self._storage_pair_factory is BoundStoragePair and type(self._storage_pair) is BoundStoragePair
                and self._storage_pair_check.__self__ is self._storage_pair
                and self._storage_pair_check.__func__ is BoundStoragePair.observe
                and type(self._storage_pair_matches) is MethodType
                and self._storage_pair_matches.__self__ is self._storage_pair
                and self._storage_pair_matches.__func__ is BoundStoragePair.matches
                and all(current is original for current, original in zip(
                    (getattr(self._storage_pair, '_BoundStoragePair__matches', None),
                     getattr(self._storage_pair, '_BoundStoragePair__observe', None)), self._storage_pair_delegates)))
            # Delegated containment uses the retained handles and original
            # registry permit, never this producer-thread status observer.
            if not cleanup:
                try: paired = self._storage_pair_matches(h.probe, self._store)
                except BoundStoragePairError: raise ForwardNativeRelayError() from None
                require(paired is True)
        require(self._facts == _canonical(dict(description=description_value(self.description),
            intention=self.intention.to_dict(), limits=asdict(self.limits), local=asdict(self.local),
            config=self.config_revision, broker=self.broker_id, storage=self.storage_epoch)))
        if self._budget is not None:
            budget, binding, raw = self._budget_refs
            require(self._budget is budget and budget.binding is binding and _canonical(binding) == raw
                and budget.store is self._store and budget._lease is self.owner_lease
                and budget.prior == self._retained_debt and budget.limits is self.limits)
        if self._relay is not None:
            require(self._relay is self._relay_ref and self._core_token is self._core_token_ref
                and self._core_methods == tuple(getattr(OpaqueForwardRelay, name) for name in
                    ('close', 'snapshot', 'prepare_close_delegation', 'close_delegated'))
                and not any(name in self._relay.__dict__ for name in
                    ('close', 'snapshot', 'prepare_close_delegation', 'close_delegated')))
        for child in self._children: child.bound()
        if not cleanup and self._registry is not None: self._registry.available(self)

    def _checked(self, callback, *args):
        self._available(); value = callback(*args); self._available(); return value

    def _sample(self, *, live=True):
        require(self._boot() == self.intention.host_boot_id)
        now = self._clock()
        require(type(now) is tuple and len(now) == 2 and all(type(v) is int and 0 <= v < 2**63 for v in now)
            and (self._last_time is None or all(a >= b for a, b in zip(now, self._last_time))))
        self._last_time = now
        if live:
            require(self.context.issued_epoch_us <= now[0] < self.context.expires_epoch_us
                    and now[1] < self.intention.application_deadline_monotonic_us)
            self.description.deadlines.check(now[1] / 1000000, now[0] / 1000000)
        return now

    def _native(self):
        h = self.resource
        require(not h._closed and not h._closing and not h._uncertain
            and h.supervisor._resource is h and h.supervisor.phase == 'active'
            and h.supervisor.broker_id == self.broker_id and h.probe.actor == h.actor
            and h.actor.chain_id == self.local.chain_id and h.actor.agent_id == h.identity.agent_id == self.local.agent_id
            and self.native_context.actor == h.actor
            and hashlib.sha256(_canonical(h.config)).hexdigest() == self.config_revision)
        require(self._checked(h.configured) is True and self._checked(h.active) is True
            and self._checked(h.consent) == self.consent and self.consent.messaging_enabled
            and self.consent.dispatch_enabled and self._checked(h.supervisor.resource_lifecycle_check) is True
            and self._checked(h.supervisor.verified_epoch) == self.storage_epoch)
        # Only the adjacent same-client views share this fresh status read.
        # Native supervisor, SDK brackets and both accounting bookends remain.
        if self._storage_pair_check is None:
            require(self._checked(h.probe.snapshot).epoch == self.storage_epoch
                and self._checked(self._store.storage.snapshot).epoch == self.storage_epoch)
        else:
            try: require(self._checked(self._storage_pair_check, self.storage_epoch) == self.storage_epoch)
            except BoundStoragePairError: raise ForwardNativeRelayError() from None
        require(self._checked(h._epoch) == self.native_context.epoch)
        for peer in (self.context.initiator, self.context.responder):
            require(any(p['chain_id'] == peer.chain_id and p['agent_id'] == peer.public_key.hex() for p in h.config['peers']))
        for scope in RELAY_SCOPES:
            require(self._checked(self.authority.authorized, self.native_context, scope, None) is True)

    def access_check(self):
        if self._observing:
            self._uncertain = True; self.fence(); raise ForwardNativeRelayError()
        self._observing = True
        try:
            self._available(); self._native(); before = self._sample()
            observation = self._checked(self.accounting.observe, self.resource, self.intention)
            after = self._sample()
            require(type(observation) is NativeAccountingObservation
                and observation.host_boot_id == self.intention.host_boot_id
                and all(a <= v <= b for a, v, b in zip(before,
                    (observation.observed_epoch_us, observation.observed_monotonic_us), after))
                and observation.retained_debt.within(self.limits))
            binding = observation.binding()
            if self._accounting_binding is None:
                self._accounting_binding = binding
                self._retained_debt = observation.retained_debt
                self._approval = relay_approval_binding(self.description, self.intention, self.limits,
                    observation, config_revision=self.config_revision, broker_id=self.broker_id,
                    storage_epoch=self.storage_epoch)
            require(binding == self._accounting_binding and self.native_context.approval_binding == self._approval)
            require(self._retained_debt == observation.retained_debt and self._approval == relay_approval_binding(
                self.description, self.intention, self.limits, observation, config_revision=self.config_revision,
                broker_id=self.broker_id, storage_epoch=self.storage_epoch))
            self._native(); self._sample(); self._available(); return True
        except BaseException:
            self._uncertain = True; self.fence(); raise
        finally: self._observing = False

    def time(self):
        self.access_check(); return self._sample()

    @contextmanager
    def _lease(self):
        from sage_lantern.storage_supervisor_socket import CaptureLease
        self.access_check()
        require(not self._held and self._native_refs[-1].acquire(blocking=False))
        self._held = True; lease = None
        try:
            self.access_check(); lease = self.resource.capture_lease(self.consent)
            self._available(); require(type(lease) is CaptureLease and lease.check() is True)
            self.access_check(); yield; self.access_check(); require(lease.check() is True)
        finally:
            try:
                if lease is not None: lease.close()
            finally: self._held = False; self._native_refs[-1].release()
        self.access_check()

    def _budget_check(self, context, intention, plan):
        require(context is self.context and intention is self.intention)
        if plan is not None:
            require(self._active is not None and any(c.plan is plan for c in self._active[1:]))
        return self.access_check()

    def budget_action_check(self, context, intention, plan):
        """One complete fresh relay observation at each original budget guard.

        Both adjacent plans remain bound to their original prepared stage. This
        does not authorize a carrier or replace its signed enrollment checks.
        """
        entered = False
        def bindings():
            self._available()
            budget = self._budget
            require(type(budget) is ForwardBudget and self._busy and budget._busy and budget._guarding
                and context is self.context is budget.context and intention is self.intention is budget.intention
                and budget._budget_action is budget._budget_action_ref is self._budget_action
                and type(self._budget_action) is MethodType
                and getattr(self._budget_action, '__self__', None) is self
                and getattr(self._budget_action, '__func__', None) is self._budget_methods[0][2]
                and type(self._budget_callbacks) is tuple and len(self._budget_callbacks) == 4
                and all(a is b for a, b in zip(self._budget_callbacks,
                    (budget._access, budget._storage, budget._native, budget._clock)))
                and budget._lease is self.owner_lease and budget.store is self._store
                and budget.key is self._native_refs[4] and budget.prior is self._retained_debt
                and budget.limits is self.limits and budget.boot_id == self.intention.host_boot_id
                and budget.slots == tuple((kind, sends) for kind in SEQUENCE for sends in (False, True))
                and self._budget_plan_methods == (ChildPlan.__post_init__, Cost.__post_init__))
            for callback, index in zip(self._budget_callbacks, (1, 1, 2, 3)):
                require(type(callback) is MethodType and callback.__self__ is self
                    and callback.__func__ is self._budget_methods[index][2])
            for target, name, method in self._budget_methods:
                current = getattr(target, name)
                require(type(current) is MethodType and current.__self__ is target
                    and getattr(current, '__func__', None) is method
                    and getattr(type(target), name) is method and name not in target.__dict__)
            require(self._budget_stage is not None)
            active, plans, encoded, stage_value = self._budget_stage
            require(self._active is active and asdict(active[0]) == stage_value
                and type(active[0].kind) is int and type(active[0].packet_bytes) is int
                and (plan is None or any(plan is original for original in plans)))
            for child, original, raw in zip(active[1:], plans, encoded):
                require(type(child) is RelayChild and child.owner is self and child.plan is original
                    and type(original) is ChildPlan and type(original.cost) is Cost
                    and _canonical(original.to_dict()) == raw)
                self._budget_plan_methods[0](original); self._budget_plan_methods[1](original.cost)
                child.bound()
            self._validate_pair(active[0], plans)
        try:
            if self._budget_observing:
                self._uncertain = True; self.fence(); raise ForwardNativeRelayError()
            self._budget_observing = entered = True
            bindings()
            require(self._budget_callbacks[0]() is True)
            bindings()
            return True
        except BaseException:
            self._uncertain = True; self.fence(); raise
        finally:
            if entered: self._budget_observing = False

    def _run(self, action):
        if self._busy:
            self._uncertain = True; self.fence(); raise ForwardNativeRelayError()
        self._busy = True
        try:
            self.access_check(); result = action(); self.access_check(); return result
        except BaseException as error:
            self._uncertain = True; self.fence()
            if not isinstance(error, Exception): raise
            raise ForwardNativeRelayError() from None
        finally: self._busy = False

    def _factory(self, child, method, *args, cleanup=False):
        self._available(cleanup=cleanup); child.bound()
        before = self._sample(live=not cleanup)
        if not cleanup: self.access_check()
        value = getattr(child.factory, method)(self, *args)
        child.bound(); self._available(cleanup=cleanup)
        after = self._sample(live=not cleanup)
        if not cleanup: self.access_check()
        if method in ('prepared', 'current', 'terminal'):
            observed = value.prepared if method == 'terminal' and type(value) is RelayTerminal else value
            require(type(observed) is RelayPrepared and observed.plan is child.plan and observed.hop is child.hop
                and observed.host_boot_id == self.intention.host_boot_id
                and all(type(v) is int and a <= v <= b for a, v, b in zip(before,
                    (observed.observed_epoch_us, observed.observed_monotonic_us), after)))
            binding = observed.binding()
            if child.prepared is None: child.prepared = binding
            require(binding == child.prepared)
        return value

    def _validate_pair(self, stage, plans):
        require(type(stage) is RelayStage and self._position < 5 and stage.kind == SEQUENCE[self._position]
            and stage.packet_bytes <= self.description.max_packet_bytes)
        endpoints = tuple(PeerID(p.chain_id, p.public_key.hex()) for p in (self.context.initiator, self.context.responder))
        first, last = endpoints if self._position % 2 == 0 else endpoints[::-1]
        require((stage.incoming.session.initiator, stage.incoming.session.responder) == (first, self.local)
            and (stage.outgoing.session.initiator, stage.outgoing.session.responder) == (self.local, last))
        for n, (plan, hop) in enumerate(zip(plans, (stage.incoming, stage.outgoing))):
            require(type(plan) is ChildPlan and plan.slot == self._position * 2 + n
                and plan.kind == stage.kind and plan.sends is bool(n)
                and plan.grant_digest == hop.grant_digest.hex()
                and plan.carrier == ('lora' if hop.carrier == 'radio' else 'tcp')
                and plan.packet_sha256 == stage.packet_sha256.hex() and plan.packet_bytes == stage.packet_bytes
                and plan.host_boot_id == self.intention.host_boot_id
                and plan.application_expires_epoch_us == self.context.expires_epoch_us
                and plan.application_deadline_monotonic_us <= self.intention.application_deadline_monotonic_us
                and plan.application_deadline_monotonic_us / 1000000 <= self.description.deadlines.application_deadline_monotonic)

    def prepare(self, stage, incoming_plan, outgoing_plan, *, incoming_factory, outgoing_factory):
        def prepare():
            require(self._active is None and incoming_factory is not outgoing_factory
                and isinstance(incoming_factory, NativeRelayFactory) and isinstance(outgoing_factory, NativeRelayFactory))
            self._validate_pair(stage, (incoming_plan, outgoing_plan))
            children = tuple(RelayChild(self, plan, hop, factory) for plan, hop, factory in zip(
                (incoming_plan, outgoing_plan), (stage.incoming, stage.outgoing), (incoming_factory, outgoing_factory)))
            self._active = (stage, *children); self._children.extend(children)
            self._budget_stage = (self._active, (incoming_plan, outgoing_plan),
                tuple(_canonical(c.plan.to_dict()) for c in children), asdict(stage))
            for child in children: self._factory(child, 'prepared', child.plan, child.hop)
            self._budget.prepare()
            for child in children:
                self._factory(child, 'prepared', child.plan, child.hop)
                child.admission = self._budget.pre_admit(child.plan)
            # Both immutable maxima are durable before any factory is entered.
            for child in children:
                self._budget.check_pending(child.admission)
                self._factory(child, 'prepared', child.plan, child.hop)
            try:
                for child in children:
                    child.registration = self._registry.begin(self, child.plan,
                        contain=self._contain_child, terminal=self._terminal_child)
                with self.owner_lease():
                    for child in children:
                        self._factory(child, 'prepared', child.plan, child.hop)
                        transfer = self._factory(child, 'open', child)
                        require(type(transfer) is ServiceTransfer)
                        child.transfer = transfer
                        child._transport_refs = child.transport_refs()
                        child._plan_bytes = _canonical(transfer.authority.plan)
                        require(describe_child(transfer, slot=child.plan.slot, kind=stage.kind) == child.plan
                            and transfer.authority.local_peer == self.local)
                        self._factory(child, 'current', child)
                for child in children:
                    self._budget.consume(child.admission)
                    self._registry.bind(child.registration, child)
                require(describe_stage(stage.kind, children[0].transfer, children[1].transfer) == stage)
                self._relay.begin(stage, children[0].transfer, children[1].transfer)
                self._bound_stage(); return children
            except BaseException:
                for child in children:
                    if child.registration is not None: self._registry.failed(child.registration)
                raise
        return self._run(prepare)

    def _bound_stage(self):
        require(self._active is not None)
        stage, *children = self._active
        self._validate_pair(stage, tuple(c.plan for c in children))
        for child in children:
            child.bound(); self._budget.check(child.admission)
            require(describe_child(child.transfer, slot=child.plan.slot, kind=stage.kind) == child.plan)
            self._factory(child, 'current', child)
            child.bound()
            require(describe_child(child.transfer, slot=child.plan.slot, kind=stage.kind) == child.plan)
        require(describe_stage(stage.kind, children[0].transfer, children[1].transfer) == stage)

    def step(self):
        def step():
            self._bound_stage()
            with self.owner_lease():
                self._bound_stage(); done = self._relay.step(); self._bound_stage()
            return done
        return self._run(step)

    def _journal_snapshot(self, child):
        journal = child._transport_refs[6]
        value = journal.snapshot()
        if type(child.transfer.flow) is BenchFlow:
            value['outcomes'] = dict(journal.db.execute('SELECT state,count(*) FROM attempts GROUP BY state'))
        child._journal = _canonical(value)
        return value

    def _terminal_child(self, plan, child):
        require(type(child) is RelayChild and child.owner is self and child.plan is plan)
        value = self._factory(child, 'terminal', child, cleanup=True)
        require(type(value) is RelayTerminal and value.resources_closed is True and value.pending_unknown is False
            and value.radio_disarmed is (True if plan.carrier == 'lora' else None)
            and child._journal is not None and type(value.journal_snapshot) is dict
            and digest(value.journal_sha256) == hashlib.sha256(_canonical(value.journal_snapshot)).hexdigest())
        import json
        prior = json.loads(child._journal); final = value.journal_snapshot
        attempts = counter(final.get('attempts'), plan.cost.radio_attempts if plan.carrier == 'lora' else plan.cost.ip_frames)
        outcomes = final.get('outcomes')
        allowed = {'RF_DONE', 'REFUSED', 'NOT_SENT'} if plan.carrier == 'lora' else {'KERNEL_ACCEPTED', 'NOT_SENT'}
        require(final.get('fenced') is False and attempts >= prior['attempts']
            and type(outcomes) is dict and set(outcomes) <= allowed
            and all(type(v) is int and 0 < v <= attempts for v in outcomes.values())
            and sum(outcomes.values()) == attempts)
        if plan.carrier == 'lora':
            require(counter(final.get('recovered_unknown'), 0) == 0
                and prior['reserved_estimated_airtime_us'] <= counter(final.get('reserved_estimated_airtime_us'), plan.cost.estimated_airtime_us))
        else:
            require(prior['reserved_stream_bytes'] <= counter(final.get('reserved_stream_bytes'), plan.cost.tx_stream_bytes)
                and prior['received_stream_bytes'] <= counter(final.get('received_stream_bytes'), plan.cost.rx_stream_bytes)
                and counter(final.get('pending_receive_stream_bytes'), 0) == counter(final.get('unknown_receive_stream_bytes'), 0) == 0
                and prior['known_kernel_accepted_stream_bytes'] <= counter(final.get('known_kernel_accepted_stream_bytes'), final['reserved_stream_bytes']))
        if child._terminal_value is not None:
            require(value.journal_sha256 == child._terminal_value.journal_sha256
                and value.retirement_evidence_sha256 == child._terminal_value.retirement_evidence_sha256)
        digest(value.retirement_evidence_sha256)
        t = child.transfer
        require(t is not None and t.flow.closed and t.authority.closed)
        journal, store = child._transport_refs[6:]
        require(journal.lock_fd is None)
        for db in (journal.db, store._db):
            if db is None: continue
            try: db.execute('SELECT 1')
            except sqlite3.ProgrammingError: pass
            else: raise ForwardNativeRelayError()
        child._terminal_value = value
        return True

    def _contain_child(self, plan, runner, deadline):
        child = next(c for c in self._children if c.plan is plan)
        require(runner is None or runner is child)
        if child.transfer is not None and child._journal is None:
            self._journal_snapshot(child)
        require(self._factory(child, 'contain', child, deadline, cleanup=True) is None)
        require(self._terminal_child(plan, child))
        child.retired = True
        return True

    def finish_stage(self):
        def finish():
            self._bound_stage()
            receipt = self._relay.finish_stage()
            for child in self._active[1:]:
                self._journal_snapshot(child)
                remaining = (child.plan.application_deadline_monotonic_us - self._sample()[1]) / 1000000
                require(remaining > 0)
                deadline = time.monotonic() + min(remaining, 30)
                with self.owner_lease():
                    require(self._factory(child, 'retire', child, deadline) is None)
                require(self._terminal_child(child.plan, child))
                child.retired = True
                require(self._registry.retired(child))
            self._active = self._budget_stage = None; self._position += 1
            return {**receipt, 'native_qualified': False, 'refunds': 0}
        return self._run(finish)

    def _registry_prepaid(self, plan):
        require(self._busy and self._active is not None and all(c.admission is not None and not c.admission.bound for c in self._active[1:]))
        require(any(c.plan is plan for c in self._active[1:])); return True

    def _registry_bind(self, plan, runner):
        require(type(runner) is RelayChild and runner.owner is self and runner.plan is plan
            and self._active is not None and runner in self._active[1:]
            and runner.admission.owner is self._budget and runner.admission.bound)
        return True

    def _draining_busy(self):
        source_busy = getattr(self.accounting, '_busy', False)
        return (self._busy or self._held or self._observing or self._budget_observing
            or self._budget is not None and self._budget._busy
            or self._relay is not None and self._relay._busy
            or type(source_busy) not in (bool, int) or source_busy != 0)

    def fence(self): self._closed = True

    def _close_core(self):
        if self._relay is None or self._core_closed: return True
        try:
            if self._owner == (os.getpid(), threading.get_ident()):
                self._relay.close()
                result = self._relay.snapshot()
                self._core_closed = result['closed'] and not result['uncertain'] and result['queued_records'] == 0
            else:
                self._available(cleanup=True)
                permit = self._registry.cleanup_permit(self, self._relay)
                require(permit.consume(self._relay) is True)
                self._core_closed = self._relay.close_delegated(self._core_token) is True
            if not self._core_closed: self._uncertain = True
            return self._core_closed
        except BaseException:
            self._uncertain = True
            raise

    def _close_local(self):
        if self._draining_busy(): return False
        core_closed = self._close_core()
        if self._budget is not None and self._budget.close() is not True: self._uncertain = True
        if any(c.registration is not None and not c.registration.retired for c in self._children): return False
        if not core_closed: return False
        if not self._source_closed:
            source, close = self._source_close
            permit = None
            if self._owner != (os.getpid(), threading.get_ident()):
                close = self._source_methods[2]
                if close is None: return False
                self._available(cleanup=True)
                permit = self._registry.cleanup_permit(self, source)
            self._source_closed = True
            try:
                result = close(source) if permit is None else close(source, permit)
                if result is not True: self._uncertain = True
            except BaseException:
                self._uncertain = True
                raise
        return not self._uncertain

    def close(self):
        require(self._owner == (os.getpid(), threading.get_ident()))
        self.fence(); return self._close_local()

    def _registry_close(self, registry):
        require(registry is self._registry and self._closed and not self._draining_busy()
                and os.getpid() == self._owner[0]
                and registry.consume_close_permit(self) is True)
        self._drain_pending = True
        self._cleanup_thread = os.getpid(), threading.get_ident()
        self._available(cleanup=True)
        return self._close_core() and not self._uncertain

    def _registry_finish_close(self, registry):
        require(registry is self._registry and self._drain_pending and not self._draining_busy())
        self._available(cleanup=True)
        return self._close_local()

    def snapshot(self):
        self._available(cleanup=True)
        return dict(completed_stages=self._position, closed=self._closed, uncertain=self._uncertain,
            prepaid_children=[c.plan.to_dict() for c in self._children if c.admission is not None],
            terminal_children=sum(c.retired for c in self._children), refunds=0,
            actual_usage=None, native_qualified=False, private_admission=False)
