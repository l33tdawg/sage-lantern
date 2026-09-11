"""Root-activated mesh factory using the real shared serial coordinator.

Root supplies independently verified operator settings, authenticated-context
authorization, registry radio/enrollment binding policy and receipt policy.
authorize_context(context, operation, action) checks the current epoch; action is
None for initial admission, or the exact OutboxAction for signed-content approval
and retry fencing. Matching an approval string alone is never sufficient.
Neither cards nor browser booleans supply those authorities. Construction never
opens a serial FD or creates a key. The supplied SerialScheduler already owns a
handshaken MeshCoreClient exclusively across processes. All producers, including
registry updates, must use this runtime. No independently writing factory may
share its actor. Mailbox scans use the coordinator's actual producer fence.

Calls are synchronous and bounded by operation_timeout; a timeout is uncertainty,
not cancellation or retry authority. User polling never dispatches outbound work.
The explicit supervisor worker separately leases each receive or single-fragment
dispatch job; only verified queued progress is eligible, never dispatching state.
Pause joins the poller AND serial queue before releasing lifecycle dependencies.
Known durable captured frames may survive background shutdown; receiving/unknown
state still denies clean shutdown. No policy lease or worker starts on import.
Policy and storage callbacks must obey bounded IO and never reenter the runtime.
"""

from contextlib import contextmanager
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from pathlib import Path
import math
import hashlib
import json
import threading
import time
import uuid

from sage_sdk import AgentIdentity, SageClient

from .mesh_contact_registry import MeshContactRegistry
from .mesh_envelope import Peer
from .mesh_inbox import MeshInbox
from .mesh_mailbox import MeshMailbox
from .mesh_outbox import MeshOutbox, SCHEMA as OUTBOX_SCHEMA
from .mesh_receipt_sender import MeshReceiptSender, ReceiptAction, ReceiptPermit, SCHEMA as RECEIPT_SCHEMA
from .mesh_receiver import MeshReceiver
from .mesh_serial_owner import SerialScheduler
from .meshcore_transport import MeshCoreClient
from .public_mesh_provider import (PublicMeshProvider, PublicMeshRejected, FRAME_PREFIX as PUBLIC_FRAME_PREFIX,
                                   PUBLIC_BURST, PUBLIC_POLL_INTERVAL)
from .message_contacts import RecipientID
from .message_storage import MessageStorageProbe
from .workflow_store import WorkflowStore


class MeshRuntimeError(RuntimeError):
    def __init__(self):
        super().__init__("mesh_runtime_unavailable")


class MeshLeaseUncertain(MeshRuntimeError):
    pass


def _require(condition):
    if condition is not True:
        raise MeshRuntimeError()


@dataclass(frozen=True)
class MeshRuntimeConfig:
    endpoint: str
    chain_id: str
    identity_path: Path
    receiver_slot: str
    operation_timeout: float = 10


@dataclass(frozen=True, repr=False)
class MeshUserContext:
    actor: RecipientID
    epoch: str
    approval_binding: str
    render_ack_binding: str = ""


@dataclass(frozen=True, repr=False)
class MeshReceiveContext:
    actor: RecipientID
    epoch: str


class MeshRuntime:
    @classmethod
    def from_config(cls, config, *, coordinator, activation_check, authorize_context,
                    verify_binding, receipt_policy, storage_lifecycle_check):
        client = None
        initialization = None
        service = None
        try:
            _require(type(config) is MeshRuntimeConfig and type(coordinator) is SerialScheduler)
            _require(all(callable(callback) for callback in (activation_check, authorize_context, verify_binding,
                                                            receipt_policy, storage_lifecycle_check)))
            _require(activation_check() is True)
            _require(type(config.operation_timeout) in (int, float) and math.isfinite(config.operation_timeout)
                     and 0 < config.operation_timeout <= 30)
            _require(isinstance(config.identity_path, Path) and config.identity_path.is_absolute())
            _require(type(config.receiver_slot) is str and str(uuid.UUID(config.receiver_slot)) == config.receiver_slot
                     and uuid.UUID(config.receiver_slot).int != 0)
            identity = AgentIdentity.from_file(str(config.identity_path))
            actor = RecipientID(config.chain_id, identity.agent_id)
            client = SageClient(config.endpoint, identity, timeout=5, trust_env=False)
            probe = MessageStorageProbe(client, actor=actor, endpoint=config.endpoint)
            probe.snapshot()
            service = cls()
            service.config, service.coordinator, service.client = config, coordinator, client
            service.actor = actor
            service._activation, service._authorize_context = activation_check, authorize_context
            service._binding, service._receipt_policy = verify_binding, receipt_policy
            service._storage_lifecycle = storage_lifecycle_check
            service._local = threading.local()
            service._stop = threading.Event()
            service._condition = threading.Condition()
            service._readers = 0
            service._poller = None
            service._receive_stop = threading.Event()
            service._background_receive = False
            service._receive_pausing = False
            service._uncertain = False
            service._closed = False
            service._public_mesh = None
            service._public_gateway = None
            service._delivery_receipts = None
            service._close_mutex = threading.Lock()
            service._scheduling_lock = threading.Lock()
            service.store = WorkflowStore(client, storage=probe)
            def initialize(device):
                service._local.mode = "initialize"
                try:
                    _require(type(device) is MeshCoreClient and service._access())
                    service._device = device
                    peer = Peer(actor.chain_id, bytes.fromhex(actor.agent_id))
                    service.registry = MeshContactRegistry(service.store, identity, owner=actor,
                        inventory=lambda: service._local.inventory, verify_binding=verify_binding)
                    service.outbox = MeshOutbox(service.store, identity, sender=peer, guard=service._outbox_guard,
                                               verify_contact=service._contact, clock=lambda: int(time.time()))
                    service.inbox = MeshInbox(service.store, recipient=peer, recipient_signing_key=identity._signing_key,
                        resolve_peer=service._resolve, access_check=service._access, storage_check=service._access)
                    service.receipts = MeshReceiptSender(service.store, identity, inbox=service.inbox, sender=peer,
                        guard=lambda action: service._access(), policy=service._receipt_permit, verify_contact=service._contact)
                    service.receiver = MeshReceiver(service.store, device, slot_id=config.receiver_slot,
                                                   access_check=service._access, storage_check=service._capture_storage)
                    service.mailbox = MeshMailbox(service.store, service.inbox, service.outbox, owner=actor,
                                                  producer_fence=service._fence)
                    service.mailbox._runtime_owned = True
                    _require(service.receiver.state() in ("idle", "captured"))
                finally:
                    service._local.__dict__.clear()
            initialization = coordinator.submit(initialize)
            initialization.result(timeout=config.operation_timeout)
            return service
        except Exception:
            if service is not None:
                service._stop.set()
                service._uncertain = True
            if initialization is not None:
                initialization.cancel()
            if client is not None:
                if initialization is not None and not initialization.done():
                    initialization.add_done_callback(lambda future: client.__exit__(None, None, None))
                else:
                    client.__exit__(None, None, None)
            raise MeshRuntimeError() from None

    def admission_safe(self):
        if any(getattr(getattr(self, name, None), "_uncertain", False)
               for name in ("receipts", "outbox", "receiver", "inbox")):
            self._uncertain = True
        return not self._uncertain and not self._closed and not self._stop.is_set()

    def _access(self):
        mode = getattr(self._local, "mode", None)
        if mode == "drain":
            return (time.monotonic() < self._local.deadline and self._local.lease() is True
                    and self._storage_lifecycle() is True and time.monotonic() < self._local.deadline)
        if not self.admission_safe() or self._activation() is not True:
            return False
        if time.monotonic() >= getattr(self._local, "public_job_deadline", math.inf):
            return False
        if mode == "initialize":
            return True
        context = getattr(self._local, "context", None)
        if type(context) is MeshReceiveContext:
            if self._local.operation not in ("receive", "background_dispatch") or (
                    self._local.receive_stop.is_set() and not self._receive_pausing):
                return False
        return (type(context) in (MeshUserContext, MeshReceiveContext) and context.actor == self.actor
                and type(context.epoch) is str and len(context.epoch) == 32
                and all(character in "0123456789abcdef" for character in context.epoch)
                and self._authorize_context(context, self._local.operation, None) is True)

    def _capture_storage(self):
        if getattr(self._local, "capture_admitted", False):
            if type(getattr(self._local, "context", None)) is MeshReceiveContext:
                return self._local.receive_storage() is True
            return self._storage_lifecycle() is True
        return self._access()

    @contextmanager
    def _context(self, context, operation):
        self._local.context, self._local.operation = context, operation
        try:
            _require(self._access())
            yield
            _require(self._access())
        finally:
            self._local.__dict__.clear()

    def _run(self, context, operation, callback):
        _require(not self._stop.is_set() and not self._closed)
        def invoke(device):
            try:
                with self._context(context, operation):
                    self._local.inventory = device.get_contacts(authorize=self._access)
                    return callback(device)
            except Exception:
                raise MeshRuntimeError() from None
        try:
            return self._submit(invoke).result(timeout=self.config.operation_timeout)
        except FutureTimeout:
            self._uncertain = True
            raise MeshRuntimeError() from None
        except Exception:
            self.admission_safe()
            raise MeshRuntimeError() from None

    def _outbox_guard(self, action):
        if not self._access():
            return False
        if getattr(self._local, "mode", None) == "drain":
            return action.operation == "read_sent"
        context = self._local.context
        if type(context) is MeshReceiveContext:
            operation = self._local.operation
            if operation == "background_dispatch" and action.operation == "dispatch":
                card = self.registry.resolve(action.radio_public_key, access_check=self._access)
                binding = hashlib.sha256(json.dumps(asdict(card), ensure_ascii=True, sort_keys=True,
                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                if binding != action.consent_binding:
                    return False
            elif operation != "receive" or action.operation != "receipt":
                return False
            return self._authorize_context(context, operation, action) is True
        return (self._authorize_context(context, self._local.operation, action) is True
                and (action.operation in ("read_sent", "receipt") or action.approval_binding == context.approval_binding))

    def _contact(self, action):
        _require(self._access())
        card = self.registry.resolve(action.radio_public_key, access_check=self._access)
        peer = getattr(action, "original_sender", None) or action.recipient
        _require(card.recipient == RecipientID(peer.chain_id, peer.public_key.hex()))
        return card

    def _resolve(self, message):
        _require(self._access())
        matches = [card for card in self.registry.list_current(access_check=self._access)
                   if bytes.fromhex(card.radio_public_key)[:6] == message.sender_prefix]
        if not matches:
            return None
        _require(len(matches) == 1)
        card = self.registry.resolve(bytes.fromhex(matches[0].radio_public_key), access_check=self._access)
        return Peer(card.recipient.chain_id, bytes.fromhex(card.recipient.agent_id))

    def _receipt_permit(self, action):
        _require(self._access())
        context = self._local.context
        if type(context) is MeshReceiveContext:
            _require(type(action) is ReceiptAction and (
                (self._local.operation == "background_dispatch" and action.operation == "dispatch")
                or (self._local.operation == "receive" and action.operation == "queue" and action.kind == "delivery")))
            permit = self._receipt_policy(context, action)
            _require(type(permit) is ReceiptPermit and permit.action == action and self._access())
            return permit
        _require(type(context) is MeshUserContext)
        permit = self._receipt_policy(context, action)
        _require(type(permit) is ReceiptPermit and permit.action == action
                 and permit.privacy_binding == context.approval_binding)
        if action.kind == "read":
            _require(bool(context.render_ack_binding) and permit.render_ack_binding == context.render_ack_binding)
        return permit

    def _submit(self, operation):
        _require(self._scheduling_lock.acquire(timeout=self.config.operation_timeout))
        try:
            return self.coordinator.submit(operation)
        finally:
            self._scheduling_lock.release()

    @contextmanager
    def _fence(self):
        deadline = time.monotonic() + self.config.operation_timeout
        _require(self._scheduling_lock.acquire(timeout=self.config.operation_timeout))
        try:
            remaining = deadline - time.monotonic()
            _require(remaining > 0)
            with self.coordinator.producer_fence(timeout=remaining) as lease:
                yield lease
        finally:
            self._scheduling_lock.release()

    def put_contact(self, context, card, *, known_actor):
        return self._run(context, "contact_update", lambda device: self.registry.put(card, known_actor=known_actor, access_check=self._access))

    def queue_message(self, context, *, message_id, recipient, radio_public_key, content, consent_binding, issued_at, expires_at,
                      expected_contact=None):
        def queue(device):
            if expected_contact is not None:
                _require(self.registry.resolve(radio_public_key, access_check=self._access) == expected_contact)
            return self.outbox.queue(message_id=message_id,
                recipient=recipient, radio_public_key=radio_public_key, content=content, approval_binding=context.approval_binding,
                consent_binding=consent_binding, issued_at=issued_at, expires_at=expires_at, access_check=self._access)
        return self._run(context, "queue_message", queue)

    def ready(self, context):
        def check(device):
            self.store.storage.snapshot()
            return self._access()
        return self._run(context, "status", check)

    def contacts(self, context):
        def read(device):
            cards = self.registry.list_current(access_check=self._access)
            cards = sorted(cards, key=lambda card: (card.chain_id, card.agent_id))[:21]
            for card in cards:
                _require(self.registry.resolve(bytes.fromhex(card.radio_public_key), access_check=self._access) == card)
            return tuple(cards)
        return self._run(context, "contacts", read)

    def queue_receipt(self, context, *, local_id, kind, radio_public_key, issued_at, expires_at):
        return self._run(context, "queue_receipt", lambda device: self.receipts.queue(local_id=local_id, kind=kind,
            radio_public_key=radio_public_key, issued_at=issued_at, expires_at=expires_at, access_check=self._access))

    def dispatch_one(self, context, identifier, *, receipt=False, radio_timestamp, expected_contact=None):
        _require(type(receipt) is bool)
        target = self.receipts if receipt else self.outbox
        def dispatch(device):
            if expected_contact is not None:
                _require(self.registry.resolve(bytes.fromhex(expected_contact.radio_public_key), access_check=self._access) == expected_contact)
            return target.dispatch_one(identifier, device=device, radio_timestamp=radio_timestamp, access_check=self._access)
        return self._run(context, "dispatch_receipt" if receipt else "dispatch_message", dispatch)

    def outbox_id(self, message_id, recipient):
        return self.mailbox._id("outbox", message_id, message_id, self.actor, recipient)

    def retry_decision(self, context, identifier, *, receipt=False, expected_revision, decision_binding):
        _require(type(receipt) is bool)
        target = self.receipts if receipt else self.outbox
        return self._run(context, "retry_receipt" if receipt else "retry_message", lambda device:
            target.retry_decision(identifier, expected_revision=expected_revision, decision_binding=decision_binding, access_check=self._access))

    def _poll(self, device):
        def consume(operation_id, message):
            probe = getattr(self._public_mesh, "contact_probe", None)
            if probe is not None and message.payload.startswith(b"LC1:"):
                return probe.ingest_frame(message, access_check=self._access) is True
            if self._public_gateway is not None:
                return self._public_gateway.ingest(device, message, access_check=self._access) is not None
            if self._public_mesh is not None and message.payload.startswith(PUBLIC_FRAME_PREFIX.encode("ascii")):
                try:
                    self._public_mesh.ingest_frame(message, access_check=self._access)
                    return True
                except PublicMeshRejected:
                    pass
            result = self.inbox.ingest(operation_id, message)
            if self._delivery_receipts is not None:
                self._delivery_receipts.after_admission(result)
            if result.status in ("admitted", "duplicate") and result.envelope_kind in ("delivery", "read"):
                original = self.inbox.historical_read(result.record_id)
                row = self.store.get(result.record_id, kind="mesh_inbound", access_check=self._access)
                self.outbox.ingest_receipt(original.original_id, wire=row["payload"]["wire"], access_check=self._access)
            return True
        state = self.receiver.state()
        if state == "idle":
            _require(self._access())
            storage = getattr(self._local, "receive_storage", self._storage_lifecycle)
            _require(storage() is True)
            self._local.capture_admitted = True
            try:
                state = self.receiver.receive_next()
            finally:
                self._local.capture_admitted = False
        _require(state in ("idle", "captured"))
        if state == "captured":
            _require(self.receiver.process_pending(consume) is True)
        return state

    def poll_once(self, context):
        return self._run(context, "receive", self._poll)

    def attach_public_mesh(self, provider):
        """Trusted root composition only, before workers; no browser activation."""
        with self._condition:
            _require(type(provider) is PublicMeshProvider and provider.workflow is self.store
                     and self._public_mesh is None and self._public_gateway is None
                     and self._poller is None and not self._stop.is_set())
            self._public_mesh = provider

    def attach_public_gateway(self, role):
        """Root-only role attachment; no second receiver or serial scheduler."""
        from .public_gateway_composition import RootPublicGateway
        with self._condition:
            _require(type(role) is RootPublicGateway and role.runtime is self
                     and role.gateway.bridge._workflow is self.store and role.gateway.scheduler is self.coordinator
                     and self._public_gateway is None and self._public_mesh is None and self._poller is None
                     and not self._readers and not self._stop.is_set() and not self._uncertain)
            self._public_gateway = role

    def public_begin(self, context, proposal, *, request_id, receipt=None):
        _require(self._public_mesh is not None)
        return self._run(context, "public_begin", lambda device: self._public_mesh.begin(proposal,
            request_id=request_id, receipt=receipt, device=device, access_check=self._access))

    def public_inspect(self, context, request_id):
        _require(self._public_mesh is not None)
        return self._run(context, "public_inspect", lambda device:
            self._public_mesh.inspect(request_id, access_check=self._access))

    def _dispatch_pending(self, device):
        if self._public_gateway is not None:
            self._public_gateway.dispatch(device, access_check=self._access)
            return
        if self._public_mesh is not None:
            probe = getattr(self._public_mesh, "contact_probe", None)
            if probe is not None and probe.active():
                for _ in range(PUBLIC_BURST):
                    if not probe.dispatch_one(device=device, access_check=self._access, inventory=self._local.inventory):
                        break
                return
            for request_id in self._public_mesh.pending_ids():
                progress, _ = self._public_mesh.inspect(request_id, access_check=self._access)
                if progress.state == "queued":
                    for _ in range(PUBLIC_BURST):
                        progress = self._public_mesh.dispatch_one(request_id, device=device, access_check=self._access,
                            inventory=self._local.inventory)
                        if progress.state != "queued":
                            break
                    return
            if self._public_mesh.active():
                return
        for row in self.store.scan(limit=50, max_records=4096, access_check=self._access):
            if row["kind"] == "mesh_outbound" and row["payload"].get("body", {}).get("schema") == RECEIPT_SCHEMA:
                body, approved = self.receipts._verified(row)
                if body["state"] != "queued" or not approved["issued_at"] <= int(time.time()) < approved["expires_at"]:
                    continue
                _require(self._access())
                action = self.receipts._action(approved, "dispatch")
                permit = self._receipt_policy(self._local.context, action)
                _require(self._access())
                if permit is None:
                    continue
                _require(type(permit) is ReceiptPermit and permit.action == action)
                self.receipts.dispatch_one(approved["receipt_id"], device=device,
                    radio_timestamp=int(time.time()), access_check=self._access)
                return
            if row["kind"] != "mesh_outbound" or row["payload"].get("body", {}).get("schema") != OUTBOX_SCHEMA:
                continue
            body = self.outbox._verified(row)
            if body["state"] != "queued" or not body["approved"]["issued_at"] <= int(time.time()) < body["approved"]["expires_at"]:
                continue
            self.outbox.dispatch_one(body["message_id"], device=device, radio_timestamp=int(time.time()), access_check=self._access)
            return

    def start_receiving(self, grant_factory, operations, *, interval=1):
        """An empty operation tuple pauses admissions; errors stop, never retry jobs."""
        _require(callable(grant_factory) and callable(operations))
        _require(type(interval) in (int, float) and math.isfinite(interval) and 0.2 <= interval <= 30)
        with self._condition:
            _require(self._poller is None and self.admission_safe() and not self._receive_pausing)
            self._background_receive = True
            self._receive_stop = threading.Event()
            stop = self._receive_stop
            gateway = self._public_gateway
            job_timeout = gateway.operation_timeout if gateway is not None else self.config.operation_timeout
            _require(type(job_timeout) in (int, float) and math.isfinite(job_timeout) and 0 < job_timeout <= 60)
            def invoke(device, operation):
                _require(not stop.is_set() and not self._stop.is_set())
                if operation not in permitted_operations():
                    return
                with grant_factory(operation) as (context, storage_check):
                    _require(type(context) is MeshReceiveContext and callable(storage_check))
                    self._local.receive_stop = stop
                    with self._context(context, operation):
                        self._local.receive_storage = storage_check
                        public_active = self._public_active()
                        if public_active or gateway is not None:
                            self._local.public_job_deadline = time.monotonic() + job_timeout
                        self._local.inventory = device.get_contacts(authorize=self._access)
                        if operation == "receive":
                            for _ in range(PUBLIC_BURST if public_active else 1):
                                if self._poll(device) == "idle":
                                    break
                        else:
                            self._dispatch_pending(device)
            def permitted_operations():
                choices = operations()
                _require(type(choices) is tuple and len(choices) <= 2
                         and all(type(choice) is str and choice in ("receive", "background_dispatch") for choice in choices)
                         and len(set(choices)) == len(choices))
                return choices
            def worker():
                index = 0
                while not stop.is_set() and not self._stop.is_set():
                    future = None
                    try:
                        choices = permitted_operations()
                        if not choices:
                            stop.wait(interval)
                            continue
                        operation = choices[index % len(choices)]
                        index += 1
                        future = self._submit(lambda device: invoke(device, operation))
                        future.result(timeout=job_timeout)
                    except Exception as error:
                        if isinstance(error, (FutureTimeout, MeshLeaseUncertain)):
                            self._uncertain = True
                        self.admission_safe()
                        if future is not None:
                            future.cancel()
                        stop.set()
                        return
                    public_active = self._public_active()
                    stop.wait(gateway.poll_interval if gateway is not None else
                              min(interval, PUBLIC_POLL_INTERVAL) if public_active else interval)
            self._poller = threading.Thread(target=worker, name="lantern-mesh-background", daemon=True)
            self._poller.start()

    def _public_active(self):
        probe = getattr(self._public_mesh, "contact_probe", None)
        return self._public_mesh is not None and (self._public_mesh.active() or probe is not None and probe.active())

    def pause_receiving(self, timeout=10):
        _require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 60)
        deadline = time.monotonic() + timeout
        with self._condition:
            self._receive_pausing = True
            self._receive_stop.set()
            poller = self._poller
        if not self._background_receive:
            with self._condition:
                self._receive_pausing = False
            return self._poller is None and self.admission_safe()
        if poller is not None:
            poller.join(max(0, deadline - time.monotonic()))
            if poller.is_alive():
                return False
        try:
            _require(time.monotonic() < deadline)
            self.coordinator.submit(lambda device: None).result(timeout=deadline - time.monotonic())
        except Exception:
            return False
        with self._condition:
            if self._poller is poller:
                self._poller = None
            self._receive_pausing = False
        return self.admission_safe()

    def start_polling(self, context, *, interval=1):
        _require(type(interval) in (int, float) and math.isfinite(interval) and 0.2 <= interval <= 30)
        with self._condition:
            _require(self._poller is None and not self._stop.is_set())
            def worker():
                while not self._stop.is_set():
                    try:
                        self.poll_once(context)
                    except Exception:
                        self._stop.set()
                        return
                    self._stop.wait(interval)
            self._poller = threading.Thread(target=worker, name="lantern-mesh-poll", daemon=True)
            self._poller.start()

    def _read(self, context, operation, callback):
        with self._condition:
            _require(not self._stop.is_set() and not self._closed)
            self._readers += 1
        try:
            with self._context(context, operation):
                return callback()
        except Exception:
            raise MeshRuntimeError() from None
        finally:
            with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    def list_messages(self, context, box, *, limit=20):
        return self._read(context, "list_messages", lambda: self.mailbox.list_messages(box, limit=limit, access_check=self._access))

    def open_message(self, context, identifier):
        return self._read(context, "open_message", lambda: self.mailbox.open_message(identifier, access_check=self._access))

    def close(self, timeout=10):
        _require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 60)
        deadline = time.monotonic()+timeout
        if not self._close_mutex.acquire(timeout=timeout):
            return False
        try:
            if self._closed:
                return True
            self._stop.set()
            self._receive_stop.set()
            if self._poller is not None:
                self._poller.join(max(0, deadline-time.monotonic()))
                if self._poller.is_alive():
                    return False
            with self._condition:
                if not self._condition.wait_for(lambda: not self._readers, max(0, deadline-time.monotonic())):
                    return False
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                return False
            self.coordinator.submit(lambda device: None).result(timeout=remaining)
            if self._public_gateway is not None:
                if self._public_gateway.close(max(0, deadline-time.monotonic())) is not True:
                    return False
            with self.coordinator.producer_fence(timeout=max(0.001, deadline-time.monotonic())) as lease:
                self._local.mode, self._local.lease = "drain", lease
                self._local.deadline = deadline
                try:
                    _require(self._access())
                    receive_state = self.receiver.state()
                    _require(receive_state == "idle" or self._background_receive and receive_state == "captured")
                    self.registry.list_current(access_check=self._access)
                    for row in self.store.scan(limit=50, max_records=4096, access_check=self._access):
                        if row["kind"] != "mesh_outbound":
                            continue
                        body = row["payload"].get("body", {})
                        if body.get("schema") == OUTBOX_SCHEMA:
                            _require(self.outbox._verified(row)["state"] != "dispatching")
                        elif body.get("schema") == RECEIPT_SCHEMA:
                            _require(self.receipts._verified(row)[0]["state"] != "dispatching")
                        elif body.get("schema") == "sage.lantern.mesh-read-consent.v1":
                            from .mesh_read_root import verify_consent_row
                            _require(verify_consent_row(row, identity=self.receipts.identity, owner=self.actor, inbox=self.inbox))
                        elif body.get("schema") == "sage.lantern.mesh-read-consent.v1":
                            from .mesh_read_root import verify_consent_row
                            _require(verify_consent_row(row, identity=self.receipts.identity, owner=self.actor, inbox=self.inbox))
                        else:
                            methods = {"sage.lantern.mesh-receipt-replay.v1": "LANTERN-OUTBOX-RECEIPT-REPLAY",
                                       "sage.lantern.mesh-receipt-slot.v1": "LANTERN-OUTBOX-RECEIPT-SLOT"}
                            _require(body.get("schema") in methods and row["revision"] == 1)
                            self.outbox._verify_proof(row["payload"], method=methods[body["schema"]])
                    _require(not self._uncertain and self._access())
                    if self._public_mesh is not None:
                        probe = getattr(self._public_mesh, "contact_probe", None)
                        if probe is not None:
                            _require(probe.drain(access_check=self._access))
                            _require(probe.close())
                        _require(self._public_mesh.drain(access_check=self._access))
                        _require(self._public_mesh.close())
                    _require(self.receiver.close(timeout=0) and self.outbox.close(timeout=0) and self.receipts.close(timeout=0))
                finally:
                    self._local.__dict__.clear()
            if time.monotonic() >= deadline or not self.coordinator.close(timeout=max(0.001, deadline-time.monotonic())):
                return False
            self.client.__exit__(None, None, None)
            self._closed = True
            return True
        except Exception:
            return False
        finally:
            self._close_mutex.release()
