# SPDX-License-Identifier: Apache-2.0
"""Finite endpoint composition; no transport opening or native qualification.

Root supplies two live, independently bound MeshActionAuthority contexts and a
concrete held native accounting source. There is deliberately no accounting
implementation or permissive fallback here. Generated keys remain in the
NoisePurpose/ForwardSession incarnation; only signed public records persist.
The original actor/operation namespace cannot regenerate after interruption.
An opaque relay, private stage provisioning and recovery admission are separate.
"""
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import os
import threading

from meshticulum.privacy.forward_session import ForwardContext
from meshticulum.privacy.mesh_envelope import Peer
from .forward_budget import Cost
from .forward_native_owner import ForwardNativeOwner, SCOPES, approval_binding, host_boot, clock
from .forward_receive_journal import ReceiveIntention
from .mesh_inbox import _canonical, _uuid
from .message_contacts import RecipientID
from .message_storage import MessageStorageProbe
from .noise_native_enrollment import NativeNoiseEnrollment, ENROLL_SCOPE
from .noise_purpose import NoisePurpose
from .workflow_store import WorkflowStore

SETUP_SCOPES = SCOPES + (ENROLL_SCOPE,)
SETUP_DOMAIN = b'sage.lantern.forward-native-setup.v1\0'


class ForwardNativeProviderError(RuntimeError):
    def __init__(self):
        super().__init__('forward_native_provider_unavailable')


def require(value):
    if not value:
        raise ForwardNativeProviderError()


def digest(value):
    require(type(value) is str and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))
    return value


@dataclass(frozen=True, repr=False)
class NativeAccountingObservation:
    """Comparison data, never a grant or proof of global/native qualification."""
    retained_debt: Cost
    actual_usage: Cost | None
    evidence_sha256: str
    host_boot_id: str
    observed_epoch_us: int
    observed_monotonic_us: int

    def __post_init__(self):
        require(type(self.retained_debt) is Cost
                and (self.actual_usage is None or type(self.actual_usage) is Cost
                     and self.actual_usage.within(self.retained_debt)))
        digest(self.evidence_sha256)
        _uuid(self.host_boot_id)
        require(all(type(v) is int and 0 <= v < 2**63 for v in
                    (self.observed_epoch_us, self.observed_monotonic_us)))

    def binding(self):
        return dict(retained_debt=asdict(self.retained_debt),
                    actual_usage=None if self.actual_usage is None else asdict(self.actual_usage),
                    evidence_sha256=self.evidence_sha256, host_boot_id=self.host_boot_id)


class NativeAccountingSource(ABC):
    """Already-held native evidence, transferred to this endpoint's lifetime.

    observe must freshly validate its exact held/named accounting inputs,
    ownership and original resource/intention binding. Its clocks must be inside
    the calling provider's clock bracket. Evidence/debt/unknown measurements are
    immutable; new child spending belongs to ForwardBudget. A public Snapshot or
    constructor-shaped DTO alone cannot implement this contract. close releases
    only this source after endpoint/key cleanup, including after permission or
    time expires; it must not infer RF retirement. There is no default source.
    """
    @abstractmethod
    def observe(self, resource, intention):
        raise NotImplementedError

    @abstractmethod
    def close(self):
        raise NotImplementedError


def setup_approval_binding(*, local, remote, local_initiator, intention,
                           issued_epoch_us, limits, accounting, config_revision,
                           broker_id, storage_epoch):
    """Pure original setup digest; live root authorization is still required."""
    require(type(local) is Peer and type(remote) is Peer and local != remote
            and type(local_initiator) is bool and type(intention) is ReceiveIntention
            and type(limits) is Cost and type(accounting) is NativeAccountingObservation
            and accounting.retained_debt.within(limits)
            and accounting.host_boot_id == intention.host_boot_id
            and type(issued_epoch_us) is int
            and 0 <= issued_epoch_us < intention.application_expires_epoch_us
            and type(broker_id) is str and len(broker_id) == 32
            and type(storage_epoch) is str and 1 <= len(storage_epoch) <= 100)
    digest(config_revision)
    body = dict(local=local.to_dict(), remote=remote.to_dict(), local_initiator=local_initiator,
                intention=intention.to_dict(), issued_epoch_us=issued_epoch_us,
                limits=asdict(limits), accounting=accounting.binding(),
                config_revision=config_revision, broker_id=broker_id, storage_epoch=storage_epoch)
    return hashlib.sha256(SETUP_DOMAIN + _canonical(body)).hexdigest()


class ForwardNativeProvider:
    """One opt-in endpoint; constructor and returned objects grant no transport.

    Keep the setup and full exchange authority contexts live through cleanup.
    Accounting ownership transfers after argument validation. generate reserves
    one original namespace. bind persists the independently approved public
    enrollment. start creates only the protected budget/journal/session; root
    must separately provision and admit every physical forward/reverse child.
    """
    def __init__(self, resource, authority, setup_context, *, remote, local_initiator,
                 intention, issued_epoch_us, limits, accounting,
                 boot_reader=host_boot, clock_reader=clock):
        from sage_lantern.mesh_daemon import HardwareRuntime
        from sage_lantern.mesh_community import MeshActionAuthority
        from sage_lantern.mesh_runtime import MeshUserContext
        require(type(resource) is HardwareRuntime and type(authority) is MeshActionAuthority
                and type(setup_context) is MeshUserContext and type(remote) is Peer
                and type(local_initiator) is bool and type(intention) is ReceiveIntention
                and type(limits) is Cost and isinstance(accounting, NativeAccountingSource)
                and callable(boot_reader) and callable(clock_reader)
                and type(issued_epoch_us) is int
                and 0 <= issued_epoch_us < intention.application_expires_epoch_us)
        self.resource, self.authority, self.setup_context = resource, authority, setup_context
        self.remote, self.local_initiator, self.intention = remote, local_initiator, intention
        self.issued_epoch_us, self.limits, self.accounting = issued_epoch_us, limits, accounting
        self._boot, self._clock = boot_reader, clock_reader
        self._process = os.getpid(), threading.get_ident()
        self._closed = self._uncertain = self._busy = self._observing = self._held = False
        self._source_closed = False
        self._purpose = self._enrollment = self._handle = self._owner = self._operation = None
        self._context = self._exchange = self._public = None
        self._components = {}
        self._last_clock = self._accounting_binding = self._setup_approval = None
        self._source_methods = (type(accounting).observe, type(accounting).close)
        self._source_observe, self._source_close = accounting.observe, accounting.close
        self._source_cleanup = (accounting, self._source_methods[1])
        self._refs = (resource, authority, setup_context, remote, local_initiator, intention,
                      limits, accounting, boot_reader, clock_reader)
        try:
            self._native_refs = (resource.supervisor, resource.probe, resource.probe.client,
                                resource.identity, resource.identity._signing_key, resource._mutex)
            self.local = Peer(resource.actor.chain_id, bytes.fromhex(resource.identity.agent_id))
            self.config_revision = hashlib.sha256(_canonical(resource.config)).hexdigest()
            self.consent = resource.consent()
            self.broker_id = resource.supervisor.broker_id
            self.storage_epoch = resource.probe.snapshot().epoch
            self._facts = (self.local, self.config_revision, self.consent, self.broker_id,
                           self.storage_epoch, issued_epoch_us, _canonical(intention.to_dict()))
            self._store = WorkflowStore(resource.probe.client, storage=MessageStorageProbe(
                resource.probe.client, actor=RecipientID(self.local.chain_id, self.local.public_key.hex()),
                endpoint=resource.probe.endpoint))
            self._components['_store'] = self._store
            self._guard()
            self._purpose = NoisePurpose(self._store, local=self.local, remote=remote,
                local_initiator=local_initiator, intention=intention,
                signing_key=self._native_refs[4], issued_epoch_us=issued_epoch_us,
                approval_digest=self._setup_approval, owner_lease=self._lease,
                access_check=self._guard, storage_check=self._guard,
                native_check=self._purpose_check, enrollment_check=self._enrollment_check,
                remote_pin_check=self._peer_check, boot_reader=boot_reader, clock=clock_reader)
            self._components['_purpose'] = self._purpose
            self._guard()
        except BaseException:
            self._uncertain = True
            self.close()
            raise

    def __repr__(self):
        return '<ForwardNativeProvider>'

    def _available(self):
        require(not self._closed and not self._uncertain and not self._source_closed
                and self._process == (os.getpid(), threading.get_ident()))
        h = self.resource
        require(all(a is b for a, b in zip(self._refs, (h, self.authority, self.setup_context,
            self.remote, self.local_initiator, self.intention, self.limits, self.accounting, self._boot, self._clock))))
        require(all(a is b for a, b in zip(self._native_refs, (h.supervisor, h.probe, h.probe.client,
            h.identity, h.identity._signing_key, h._mutex))))
        require(self._source_methods == (type(self.accounting).observe, type(self.accounting).close)
                and 'observe' not in self.accounting.__dict__ and 'close' not in self.accounting.__dict__)
        require(self._source_observe.__self__ is self.accounting
                and self._source_observe.__func__ is self._source_methods[0]
                and self._source_close.__self__ is self.accounting
                and self._source_close.__func__ is self._source_methods[1]
                and self._source_cleanup[0] is self.accounting
                and self._source_cleanup[1] is self._source_methods[1])
        require(all(getattr(self, name) is value for name, value in self._components.items()))
        require(self._facts == (self.local, self.config_revision, self.consent, self.broker_id,
                self.storage_epoch, self.issued_epoch_us, _canonical(self.intention.to_dict())))
        require(h.supervisor._resource is h and h.supervisor.phase == 'active'
                and not h._closed and not h._closing and not h._uncertain
                and h.probe.actor == h.actor and h.actor.chain_id == self.local.chain_id
                and h.actor.agent_id == h.identity.agent_id == self.local.public_key.hex()
                and self.setup_context.actor == h.actor
                and hashlib.sha256(_canonical(h.config)).hexdigest() == self.config_revision)
        if self._setup_approval is not None:
            require(self.setup_context.approval_binding == self._setup_approval)

    def _checked(self, callback, *args):
        self._available()
        result = callback(*args)
        self._available()
        return result

    def _time(self):
        require(self._checked(self._boot) == self.intention.host_boot_id)
        now = self._checked(self._clock)
        require(type(now) is tuple and len(now) == 2
                and all(type(n) is int and 0 <= n < 2**63 for n in now)
                and (self._last_clock is None or all(a >= b for a, b in zip(now, self._last_clock)))
                and self.issued_epoch_us <= now[0] < self.intention.application_expires_epoch_us
                and now[1] < self.intention.application_deadline_monotonic_us)
        self._last_clock = now
        return now

    def _guard(self):
        if self._observing:
            self._uncertain = True
            raise ForwardNativeProviderError()
        self._observing = True
        try:
            h = self.resource
            self._available()
            require(self._checked(h.configured) is True and self._checked(h.active) is True
                    and self._checked(h.consent) == self.consent
                    and self.consent.messaging_enabled is True and self.consent.dispatch_enabled is True
                    and h.supervisor.broker_id == self.broker_id
                    and self._checked(h.supervisor.resource_lifecycle_check) is True
                    and self._checked(h.supervisor.verified_epoch) == self.storage_epoch
                    and self._checked(h.probe.snapshot).epoch == self.storage_epoch
                    and self._checked(self._store.storage.snapshot).epoch == self.storage_epoch
                    and self._checked(h._epoch) == self.setup_context.epoch
                    and any(p['chain_id'] == self.remote.chain_id
                            and p['agent_id'] == self.remote.public_key.hex() for p in h.config['peers']))
            for scope in SETUP_SCOPES:
                require(self._checked(self.authority.authorized, self.setup_context, scope, None) is True)
            before = self._time()
            observed = self._checked(self._source_observe, h, self.intention)
            after = self._time()
            require(type(observed) is NativeAccountingObservation
                    and observed.host_boot_id == self.intention.host_boot_id
                    and before[0] <= observed.observed_epoch_us <= after[0]
                    and before[1] <= observed.observed_monotonic_us <= after[1]
                    and observed.retained_debt.within(self.limits))
            binding = _canonical(observed.binding())
            require(self._accounting_binding is None or binding == self._accounting_binding)
            approved = setup_approval_binding(local=self.local, remote=self.remote,
                local_initiator=self.local_initiator, intention=self.intention,
                issued_epoch_us=self.issued_epoch_us, limits=self.limits, accounting=observed,
                config_revision=self.config_revision, broker_id=self.broker_id, storage_epoch=self.storage_epoch)
            require(self.setup_context.approval_binding == approved)
            if self._accounting_binding is None:
                self._accounting_binding, self._setup_approval = binding, approved
                self._retained_debt = observed.retained_debt
            require(self._retained_debt == observed.retained_debt)
            if self._exchange is not None:
                require(self._exchange.actor == h.actor and self._exchange.epoch == self.setup_context.epoch
                        and self._exchange.approval_binding == approval_binding(self._context, self.intention,
                            self.local_initiator, config_revision=self.config_revision,
                            broker_id=self.broker_id, storage_epoch=self.storage_epoch))
                for scope in SETUP_SCOPES:
                    require(self._checked(self.authority.authorized, self._exchange, scope, None) is True)
            self._available()
            return True
        except BaseException:
            self._uncertain = self._closed = True
            raise
        finally:
            self._observing = False
            if self._closed:
                self.close()

    @contextmanager
    def _lease(self):
        from sage_lantern.storage_supervisor_socket import CaptureLease
        self._guard()
        require(not self._held and self._native_refs[-1].acquire(blocking=False))
        self._held = True
        lease = None
        try:
            self._guard()
            lease = self.resource.capture_lease(self.consent)
            self._available()
            require(type(lease) is CaptureLease and lease.check() is True)
            self._guard()
            yield
            self._guard()
            require(lease.check() is True)
        finally:
            try:
                if lease is not None:
                    lease.close()
            finally:
                self._held = False
                self._native_refs[-1].release()
        self._guard()

    def _purpose_check(self, binding):
        require(self._purpose is not None and binding is self._purpose.binding)
        return self._guard()

    def _enrollment_check(self, info, context_digest):
        self._guard()
        require(self._enrollment is not None)
        result = self._enrollment.enrollment_check(info, context_digest)
        self._guard()
        return result

    def _peer_check(self, peer, public_key, context_digest):
        self._guard()
        require(self._enrollment is not None)
        result = self._enrollment.peer_key_check(peer, public_key, context_digest)
        self._guard()
        return result

    def _run(self, action):
        if self._busy:
            self._uncertain = True
            self.close()
            raise ForwardNativeProviderError()
        self._busy = True
        try:
            self._guard()
            result = action()
            self._guard()
            return result
        except BaseException as error:
            self._uncertain = True
            self.close()
            if not isinstance(error, Exception):
                raise
            raise ForwardNativeProviderError() from None
        finally:
            self._busy = False
            if self._closed:
                self.close()

    def generate(self):
        def generate():
            require(self._public is None)
            self._public = self._purpose.generate()
            self._components['_public'] = self._public
            return self._public
        return self._run(generate)

    def bind(self, context, exchange_context, *, remote_generation_id):
        from sage_lantern.mesh_runtime import MeshUserContext
        def bind():
            require(type(context) is ForwardContext and type(exchange_context) is MeshUserContext
                    and self._public is not None and self._context is None)
            self._context, self._exchange = context, exchange_context
            self._components.update(_context=context, _exchange=exchange_context)
            self._guard()
            self._enrollment = NativeNoiseEnrollment(self.resource, self.authority, exchange_context,
                self._store, context=context, intention=self.intention, local_initiator=self.local_initiator,
                local_public=self._public, remote_generation_id=remote_generation_id,
                boot_reader=self._boot, clock_reader=self._clock)
            self._components['_enrollment'] = self._enrollment
            with self._lease():
                require(self._enrollment.enroll() is True)
            self._handle = self._purpose.bind(context)
            self._components['_handle'] = self._handle
            return True
        return self._run(bind)

    def _abort(self):
        if self._owner is None:
            return True
        if any(getattr(self._owner, name, False) for name in ('_busy', '_held', '_constructing')):
            self._owner.fence()
            return False
        return self._owner.close()

    def start(self, sealed_record=None):
        def start():
            require(self._handle is not None and self._owner is None and self._operation is None)
            def create(key):
                # Retain cleanup ownership even if native construction refuses.
                self._owner = object.__new__(ForwardNativeOwner)
                self._components['_owner'] = self._owner
                self._owner.__init__(self.resource, self.authority, self._exchange,
                    context=self._context, intention=self.intention, local_initiator=self.local_initiator,
                    key_check=self._purpose.key_check, peer_key_check=self._purpose.peer_key_check,
                    boot_reader=self._boot, clock_reader=self._clock)
                return self._owner.operation(key, limits=self.limits,
                    prior_debt=self._retained_debt, sealed_record=sealed_record)
            self._operation = self._handle.start(create, abort=self._abort)
            self._components['_operation'] = self._operation
            return self._operation
        return self._run(start)

    @property
    def owner(self):
        def owner():
            require(self._owner is not None)
            self._owner.access_check()
            return self._owner
        return self._run(owner)

    @property
    def operation(self):
        self.owner
        require(self._operation is not None and self._owner._operation is self._operation)
        return self._operation

    def retire(self):
        """Persist public retirement only while original permission is live."""
        result = self._run(lambda: self._purpose.retire())
        require(self.close() is True)
        return result

    def close(self):
        """Cancel locally; retain accounting until endpoint/key cleanup joins.

        False is not retirement or handoff proof. Physical children must have
        their separately owned terminal cleanup; this method never opens or
        drains a transport, renews permission or deletes durable debt/claims.
        """
        self._closed = True
        try:
            purpose_ok = self._purpose is None or self._purpose.close()
        except Exception:
            purpose_ok = False
            self._uncertain = True
        if self._busy or self._observing or self._held:
            return False
        try:
            endpoint_ok = self._abort()
        except Exception:
            endpoint_ok = False
            self._uncertain = True
        key_busy = self._purpose is not None and (self._purpose._busy is not None
            or self._purpose._observing or self._purpose._factory_active)
        endpoint_busy = self._owner is not None and any(getattr(self._owner, name, False)
            for name in ('_busy', '_held', '_constructing'))
        if self._owner is not None and self._owner._registry is not None:
            endpoint_busy = endpoint_busy or any(c.owner is self._owner and not c.retired
                for c in self._owner._registry._children)
        enrollment_busy = self._enrollment is not None and self._enrollment._busy
        enrollment_ok = self._enrollment is None
        if not endpoint_busy and not key_busy and not enrollment_busy:
            try:
                enrollment_ok = self._enrollment is None or self._enrollment.close() is True
            except Exception:
                self._uncertain = True
        if not endpoint_busy and not key_busy and not enrollment_busy and not self._source_closed:
            self._source_closed = True
            try:
                source_ok = self._source_cleanup[1](self._source_cleanup[0]) is True
            except Exception:
                source_ok = False
            self._uncertain = self._uncertain or not source_ok
        return (purpose_ok and endpoint_ok and enrollment_ok and not key_busy and self._source_closed
                and not self._uncertain and self._process == (os.getpid(), threading.get_ident()))
