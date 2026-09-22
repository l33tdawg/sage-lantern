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

A background job that fails before it reached a destructive radio step cannot
have sent or dequeued anything, so it stops the poller without latching the
permanent uncertainty flag: the next authenticated request restarts the reviewed
worker. One exception is an admission refusal - a live producer fence, which an
owner-session mailbox read holds for as long as its body runs. That admits
nothing, so the worker waits it out and keeps receiving instead of retiring, and
the mailbox read must never hold a runtime lock the worker needs to submit. A
failure that may have written to the radio, a capture lease whose outcome is
unknown, or any poisoned transport stays fail-closed, and no capture is started
unless the job's own deadline still has room to finish it.
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

from .airtime_budget import AirtimeLedger, AirtimePolicy
from .mesh_custodian import CustodianError, MeshCustodian
from .mesh_contact_registry import MeshContactRegistry
from .mesh_envelope import Peer
from .mesh_fragments import split_envelope
from .mesh_inbox import MeshInbox
from .mesh_mailbox import MeshMailbox
from .mesh_outbox import MeshOutbox, SCHEMA as OUTBOX_SCHEMA
from .mesh_receipt_sender import MeshReceiptSender, ReceiptAction, ReceiptPermit, SCHEMA as RECEIPT_SCHEMA
from .mesh_receiver import MeshReceiver
from .mesh_serial_owner import SerialScheduler, SerialOwnerError
from .meshcore_transport import WIRE_RESYNC_CODES, MeshCoreClient
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


# A capture issues one destructive firmware dequeue plus its durable CAS. Never
# start one without at least this much of the background job deadline left (at
# most half the job budget), so a slow pre-capture check cannot turn into an
# abandoned mid-exchange operation.
CAPTURE_RESERVE_SECONDS = 5.0

# Coordinator refusals that admit nothing and are reversible. They are the
# normal consequence of a live producer fence, which an owner-session mailbox
# read holds for the length of its body; a worker that retired on one stopped
# receiving for as long as the session kept reading (unit A journal 2026-09-13
# 10:44-10:48). producer_drain_timeout is deliberately absent: that one is
# containment, not admission, and must stay fail-closed.
ADMISSION_REFUSAL_CODES = ("producer_admission_fenced", "producer_queue_full")
BACKGROUND_RETRY_MIN_SECONDS = 0.5
BACKGROUND_RETRY_MAX_SECONDS = 5.0
# Fragments of one message sent per background wake. Wakes alternate with the
# receive path and sleep `interval` between them, so one fragment per wake made a
# multi-fragment message cost a wake - at least two seconds of pacing - for every
# 128 bytes, whatever the link could actually carry. The firmware's own TX queue
# is the real bound and it is much deeper than this; the public-mesh path in this
# file has always sent PUBLIC_BURST frames per wake for the same reason.
DISPATCH_BURST = 4
# Stop bursting this far before the job deadline, so a fragment is never started
# that the wake cannot finish. A fragment cut off mid-flight is an unknown
# outcome, and while the outbox makes it retryable inside the row's window it is
# still evidence we would rather not manufacture.
DISPATCH_RESERVE_SECONDS = 1.0
HANDOVER_BURST = 4
HANDOVER_RETRY_SECONDS = 300.0
# Retry cadence for a message row whose fragment reached the wire and was never
# acknowledged. Owner decision 2026-09-13: that outcome is retryable inside the
# row's own 24-hour window, escalated only once the window lapses. One attempt
# per row per wake, backing off along this schedule (seconds).
RETRY_BACKOFF_SECONDS = (60, 300, 900, 3600)
# Bounded wait for a timed-out job to observe the stop fence and unwind so its
# radio call cannot overlap the retry decision that follows it.
ABANDONED_JOB_SECONDS = 5.0
# Audit marker for a decision this runtime made by itself rather than an operator:
# it travels with the row as the retry binding.
AUTO_RETRY_BINDING = "lantern-auto-retry"
# An owner request (a send) must be able to wait out a competing mailbox read,
# which holds the producer fence for the length of its body. The background
# worker never waits here: it has its own retry loop and must not consume a
# caller's deadline.
SUBMIT_RETRY_SECONDS = 0.2
# Every operation refreshes the contact inventory before it touches the radio, and
# a read-only contact-list stall does not poison the transport: the port is
# resynchronised and the firmware re-sends a whole record for the next read. One
# bounded re-read therefore turns "this attempt died on a 1-in-30 stall" into
# "this attempt was slower" - without it, unit A answered an owner send with
# 503/community_unavailable before the queue write and could never dispatch the
# row it did eventually create (unit A journal 2026-09-13 16:19-16:22).
INVENTORY_ATTEMPTS = 3
# Wall-clock bound on the refresh as well as on the attempts. Each stalled
# contact read costs the transport's own timeout (3 s), so three attempts of a
# part-way stall add up to the whole background job budget: unit A's dispatch
# wakes died on the job deadline (mesh_background_failure TimeoutError,
# operation=background_dispatch, wire_frames=1 - the contact-read command frame -)
# before a fragment could go out, the worker retired and the runtime latched
# until the next authenticated request. The refresh therefore has to leave this
# much of the operation for the step it exists to serve; when the budget runs
# out the cached inventory is used exactly as an exhausted re-read would be.
# The job's own deadline is what the worker enforces, so the reserve has to be
# big enough that an abandoned refresh can never be the last thing the job does:
# unit A's dispatch wake at 17:18 on 2026-09-18 reached dispatch_one and then
# failed on the session's own expiry with a whole fragment still unsent.
INVENTORY_RESERVE_SECONDS = 6.0
# A background wake may reuse the last inventory any operation read when it is
# this fresh; only a stale one is read again. The firmware's routing table
# changes only on an import or an observed advertisement, and every wake that
# pays the serial round trip risks its 3 s stall and the job budget with it
# (unit A journal 2026-09-18 17:53: a fenced read answered 503 while the wake
# held the serial owner). The row's peer must still be in the inventory for the
# prefix to be unique, so a stale table refuses rather than misfires.
INVENTORY_WAKE_FRESH_SECONDS = 20.0
# A full journal scan costs the whole background budget on this device (every
# page is a snapshot pair plus a list call), so a wake only pays it this often.
# Rows the runtime queued dispatch from memory and never wait for a scan, and
# reconciliation loads the rows it already knows by key.
DISPATCH_SCAN_SECONDS = 30.0
# A read-only exchange that came back as a complete but unexpected frame leaves
# the port on a frame boundary, so a fresh read is safe even though there is
# nothing to discard: the abandoned response belongs to the read that just
# failed. Unit A's stock firmware answers the contact-list read this way often
# enough that requiring a discarded partial frame (WIRE_RESYNC_CODES plus
# discarded bytes) left every dispatch and import failing on the first hiccup.
READ_RETRY_CODES = ("unexpected_response",)
# A masked error still carries the exception it replaced in __context__ even though
# `raise ... from None` hides it, and every layer between the radio and the worker
# masks: mesh_outbox._admitted, mesh_outbox.dispatch_one and MeshRuntime._run. The
# traceback of a mask starts at the mask itself, so unit A journalled
# "MeshOutboxError ... mesh_outbox.py:91:call" at 17:34:34 on 2026-09-13 with no way
# to tell a refused admission from a stalled contact read. Walk the chain instead.
# Depth 4 was measured too shallow on the first live failure (17:46): the chain ran
# MeshOutboxError -> RegistryError -> WorkflowUnavailable twice and stopped one hop
# short of the store call that actually failed.
CAUSE_DEPTH = 8
CAUSE_FRAMES = 5


def _require(condition):
    if condition is not True:
        raise MeshRuntimeError()


def _masked_causes(error):
    """The exceptions a `raise ... from None` chain hid, outermost first."""
    causes = []
    seen = {id(error)}
    current = error
    for _ in range(CAUSE_DEPTH):
        inner = getattr(current, '__cause__', None)
        if inner is None:
            inner = getattr(current, '__context__', None)
        if not isinstance(inner, Exception) or id(inner) in seen:
            break
        seen.add(id(inner))
        causes.append(inner)
        current = inner
    return causes


def _failure_frames(error, limit):
    import traceback
    return [dict(file=Path(frame.filename).name, function=frame.name, line=frame.lineno)
            for frame in traceback.extract_tb(error.__traceback__)[-limit:]]


def _failure_code(error):
    """The bounded error code a mesh error carries, when it is a plain short string."""
    code = getattr(error, 'code', None)
    return code if type(code) is str and 0 < len(code) <= 32 else None


def _log_operation_failure(error, operation, *, provably_pre_wire=False, wire_frames=0,
                           access_denied=None, fenced_rows=(), event="mesh_operation_failed"):
    """Bounded diagnostics only: no payloads, text, keys or peer identities."""
    try:
        import syslog
    except Exception:
        return
    try:
        causes = []
        for cause in _masked_causes(error):
            code = _failure_code(cause)
            causes.append(dict(**{'exception': type(cause).__name__},
                               **({} if code is None else {'code': code}),
                               frames=_failure_frames(cause, CAUSE_FRAMES)))
        payload = dict(event=event, exception=type(error).__name__, operation=operation,
                       pre_wire=provably_pre_wire, wire_frames=wire_frames,
                       frames=_failure_frames(error, 12))
        if access_denied is not None:
            payload['access_denied'] = access_denied
        if fenced_rows:
            # The row whose unknown outcome fences admission, by message id: the
            # one fact a refused read needs and could never state (unit A,
            # 2026-09-22 - every community route answered 503 while the journal
            # named neither the fence nor the row that set it).
            payload['fenced_rows'] = [str(identifier) for identifier in fenced_rows][:8]
        if causes:
            payload['causes'] = causes
        syslog.syslog(syslog.LOG_ERR, json.dumps(payload))
    except Exception:
        return


def _log_background_failure(error, operation, provably_pre_wire, wire_frames, access_denied=None, fenced_rows=()):
    _log_operation_failure(error, operation, provably_pre_wire=provably_pre_wire,
                           wire_frames=wire_frames, access_denied=access_denied, fenced_rows=fenced_rows,
                           event="mesh_background_failure")


def _log_airtime_deferral(ledger, count):
    """Bounded diagnostics: the duty-cycle gate paced one dispatch wake."""
    try:
        import syslog
    except Exception:
        return
    try:
        payload = dict(count=count, **ledger.snapshot())
        payload["event"] = "mesh_airtime_deferred"
        syslog.syslog(syslog.LOG_INFO, json.dumps(payload))
    except Exception:
        return


class _BackgroundJob:
    """Per-job marker for the destructive radio steps a poll can reach.

    It is set immediately before the destructive firmware dequeue or a fragment
    dispatch, after the last authorization check of that step. A failure that
    never set it proves this job did not change radio or firmware state, so the
    worker can retire without latching process-wide uncertainty.

    access_denied names the guard condition that last refused this job, because a
    dispatch re-evaluates authorization between loading the row and writing the
    fragment: unit A 2026-09-13 17:50 lost a dispatch five masks deep in a plain
    MeshOutboxError whose innermost cause was RegistryError, which cannot be told
    apart from a contact or store failure without the condition itself.

    fenced_rows carries the message ids of the outbox rows whose unknown outcome
    is what refused it, when the refusal was `admission_unsafe`: an admission
    fence is the one refusal whose cause is a durable row rather than a condition
    of this process (unit A, 2026-09-22).
    """

    __slots__ = ("radio_action", "access_denied", "fenced_rows")

    def __init__(self):
        self.radio_action = threading.Event()
        self.access_denied = None
        self.fenced_rows = ()


@dataclass(frozen=True)
class MeshRuntimeConfig:
    endpoint: str
    chain_id: str
    identity_path: Path
    receiver_slot: str
    operation_timeout: float = 10
    airtime: AirtimePolicy | None = None
    # None disables store-and-forward custody; a non-empty tuple of
    # "chain_id:radio-public-key" strings enables it for those depositors only.
    custody: tuple[str, ...] | None = None


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
            _require(config.airtime is None or type(config.airtime) is AirtimePolicy)
            _require(config.custody is None or (type(config.custody) is tuple
                     and 1 <= len(config.custody) <= 64
                     and all(type(item) is str for item in config.custody)))
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
            service._denial = threading.local()
            service._fence_snapshot = None
            service._poller = None
            service._receive_stop = threading.Event()
            service._background_receive = False
            service._receive_pausing = False
            service._uncertain = False
            service._closed = False
            service._public_mesh = None
            service._public_gateway = None
            service._delivery_receipts = None
            service._airtime = None if config.airtime is None else AirtimeLedger(config.airtime)
            service._airtime_deferrals = 0
            service._close_mutex = threading.Lock()
            service.store = WorkflowStore(client, storage=probe)
            try:
                service._custodian = None if config.custody is None else MeshCustodian(
                    service.store, access_check=service._access, depositors=config.custody)
            except CustodianError:
                raise ValueError("invalid_custody_policy") from None
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
                        resolve_peer=service._resolve, access_check=service._access, storage_check=service._access,
                        custody=getattr(service, "_custodian", None))
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
        fence = {"layers": [], "rows": []}
        for name in ("receipts", "outbox", "receiver", "inbox"):
            target = getattr(self, name, None)
            if target is None:
                continue
            latching = getattr(target, "latching_unknown", None)
            # An outbox row whose fragment reached the wire is retryable inside
            # its own signed window (owner decision 2026-09-13) and must not
            # park the unit at not_ready until a human restarts it; its window
            # lapse, or any unknown that cannot be reconciled, still latches.
            unknown = latching() is True if callable(latching) else bool(getattr(target, "_uncertain", False))
            if unknown:
                self._uncertain = True
                fence["layers"].append(name)
                rows = getattr(target, "fencing_unknowns", None)
                if callable(rows):
                    fence["rows"].extend(rows())
        self._fence_snapshot = fence if fence["layers"] else None
        return not self._uncertain and not self._closed and not self._stop.is_set()

    def admission_fence(self):
        """Which layer, and which outbox rows, fence admission: bounded, no payloads.

        `_access` refuses with a bare False and `_read` used to raise
        `MeshRuntimeError() from None`, so a refused community route left the
        journal naming neither the conjunct nor the row behind it (unit A,
        2026-09-22: six `dispatching` rows and every mailbox read answering 503).
        The snapshot is refreshed by every admission check, and it *names the
        row* whenever the fence is a durable outbox row rather than a condition
        of this process.
        """
        fence = getattr(self, "_fence_snapshot", None)
        if not fence:
            return None
        return {"layers": sorted(set(fence["layers"])), "rows": sorted({str(item) for item in fence["rows"]})}

    def _radio_written(self):
        """Command frames this runtime's radio has begun writing, or None."""
        device = getattr(self, "_device", None)
        value = getattr(device, "wire_frames", None)
        return value if type(value) is int else None

    def _radio_poisoned(self):
        """True when the transport is poisoned or cannot report its own state."""
        device = getattr(self, "_device", None)
        value = getattr(device, "poisoned", None)
        return value is not False

    def _capture_deadline_open(self):
        """False when this poll cannot both start and finish a capture in time.

        Only background jobs carry a deadline; an explicit user poll keeps its
        existing single-operation timeout.
        """
        deadline = getattr(self._local, "background_deadline", None)
        if deadline is None:
            return True
        reserve = getattr(self._local, "capture_reserve", CAPTURE_RESERVE_SECONDS)
        return time.monotonic() + reserve <= deadline

    def _access(self):
        mode = getattr(self._local, "mode", None)
        if mode == "drain":
            if time.monotonic() >= self._local.deadline:
                return self._denied("drain_deadline")
            if self._local.lease() is not True:
                return self._denied("drain_lease")
            if self._storage_lifecycle() is not True:
                return self._denied("drain_storage")
            if time.monotonic() >= self._local.deadline:
                return self._denied("drain_deadline")
            return True
        if not self.admission_safe():
            return self._denied("admission_unsafe")
        if self._activation() is not True:
            return self._denied("not_active")
        if time.monotonic() >= getattr(self._local, "public_job_deadline", math.inf):
            return self._denied("job_deadline")
        if mode == "initialize":
            return True
        context = getattr(self._local, "context", None)
        if type(context) is MeshReceiveContext:
            if self._local.operation not in ("receive", "background_dispatch"):
                return self._denied("operation_not_permitted")
            if self._local.receive_stop.is_set() and not self._receive_pausing:
                return self._denied("receive_stopped")
        if type(context) not in (MeshUserContext, MeshReceiveContext):
            return self._denied("no_context")
        if context.actor != self.actor:
            return self._denied("context_actor")
        if (type(context.epoch) is not str or len(context.epoch) != 32
                or not all(character in "0123456789abcdef" for character in context.epoch)):
            return self._denied("context_epoch")
        if self._authorize_context(context, self._local.operation, None) is not True:
            return self._denied("authorize_context")
        return True

    def _denied(self, reason):
        """Record why this refusal happened, then refuse.

        The marker belongs to the job, not to the thread: _context clears the
        thread-local state as it unwinds, so by the time the worker logs the
        failure the only surviving record is the one held by the job itself.
        The refusal also goes to a thread-local that _context does not clear, so
        a mailbox read - which runs in the caller's thread, not a job - can name
        its own conjunct when it logs the failure.
        """
        job = getattr(self._local, "job", None)
        if type(job) is _BackgroundJob:
            job.access_denied = reason
            if reason == "admission_unsafe":
                self.admission_safe()
                fence = self.admission_fence() or {}
                job.fenced_rows = tuple(fence.get("rows") or ())
        denial = getattr(self, "_denial", None)
        if denial is not None:
            denial.reason = reason
        return False

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
                    self._local.inventory = self._inventory(device, self._access)
                    return callback(device)
            except Exception as error:
                # Name the real cause: the MeshRuntimeError a caller sees is the
                # same for a stalled transport, a refused admission and a failed
                # compare-and-swap, so without this line a pre-mutation refusal
                # leaves no trace of which one happened.
                _log_operation_failure(error, operation)
                raise MeshRuntimeError() from None
        try:
            deadline = time.monotonic() + self.config.operation_timeout
            return self._submit(invoke, deadline=deadline).result(timeout=max(0.001, deadline - time.monotonic()))
        except FutureTimeout as error:
            # A timeout is only process-wide-uncertain when it can have changed
            # radio or firmware state. The per-layer flags record that precisely
            # (the outbox sets its own flag only after a fragment reached the
            # wire), so defer to them instead of latching on every timeout.
            _log_operation_failure(error, operation)
            self.admission_safe()
            raise MeshRuntimeError() from None
        except Exception as error:
            if type(error) is not MeshRuntimeError:
                _log_operation_failure(error, operation)
            self.admission_safe()
            raise MeshRuntimeError() from None

    def _resynced_read_failure(self, device):
        """True when the last radio failure was read-only and may be re-read.

        Two shapes qualify, and both are provably read-only in the sense that
        matters: the exchange was not sending and not receiving, so it cannot
        have put user traffic on the air or dequeued it from the radio. Either
        the port was mid-stream and the transport discarded the abandoned tail
        (the firmware is back on a record boundary), or the response was a
        complete but unexpected frame (the firmware never left a boundary).
        Anything else - a poisoned transport, an exchange that could have sent or
        dequeued, or a refusal with no classification - keeps the single-attempt
        rule.
        """
        failure = getattr(device, "read_failure", None)
        if (getattr(device, "poisoned", None) is not False
                or type(failure) is not dict or type(failure.get("error")) is not str
                or failure.get("sending") is not False or failure.get("receiving") is not False):
            return False
        if failure["error"] in READ_RETRY_CODES:
            return True
        return (failure["error"] in WIRE_RESYNC_CODES
                and getattr(device, "discarded_bytes", 0) > 0)

    def _readonly_read_failure(self, device):
        """True when the last radio failure provably read and wrote nothing.

        A failed read cannot have changed the firmware's routing table, so the
        last inventory this runtime read is still the best observation of it.
        This is deliberately weaker than ``_resynced_read_failure``: re-reading
        the port needs a proven frame boundary, but *reusing* an inventory only
        needs the failure to be read-only. A poisoned transport, a failure on a
        send or receive exchange, and a refusal with no classification all keep
        failing closed.
        """
        failure = getattr(device, "read_failure", None)
        return (getattr(device, "poisoned", None) is False
                and type(failure) is dict and type(failure.get("error")) is str
                and failure.get("sending") is False and failure.get("receiving") is False)

    def _inventory(self, device, authorize, *, fresh=0.0):
        """Bounded contact-inventory refresh, falling back to the last known one.

        Every radio operation refreshes the inventory first, so one failed
        refresh used to fail an owner send before its queue write and every
        dispatch before a fragment went out. Peer identity and consent come from
        the registry; the firmware contact table only routes to them, and a
        dispatch whose peer is missing from a stale table refuses rather than
        sending. A refresh that fails after its bounded re-read therefore falls
        back to the inventory this runtime read earlier and still holds; a first
        read with nothing cached keeps failing closed. The re-read is bounded by
        the operation's own clock too (INVENTORY_RESERVE_SECONDS), so a stall
        that would have consumed the whole job cannot take the wire step with it.
        The last inventory any operation read is kept on the runtime itself: the
        job-local one is cleared with every context, and a provably read-only
        failure is the only thing allowed to reuse it. A caller that passes a
        ``fresh`` window may reuse it without touching the serial at all.
        """
        failure = None
        rereadable = False
        cached = getattr(self, "_last_inventory", None)
        cached_at = getattr(self, "_last_inventory_at", None)
        if (fresh and cached is not None and type(cached_at) in (int, float)
                and 0 <= time.monotonic() - cached_at <= fresh):
            return cached
        deadline = getattr(getattr(self, "_local", None), "background_deadline", None)
        if type(deadline) not in (int, float):
            timeout = getattr(getattr(self, "config", None), "operation_timeout", None)
            deadline = time.monotonic() + (timeout if type(timeout) in (int, float) else 10.0)
        limit = deadline - INVENTORY_RESERVE_SECONDS
        for attempt in range(INVENTORY_ATTEMPTS):
            try:
                inventory = device.get_contacts(authorize=authorize)
                self._last_inventory = inventory
                self._last_inventory_at = time.monotonic()
                return inventory
            except Exception as error:
                failure = error
                rereadable = self._resynced_read_failure(device)
                if attempt == INVENTORY_ATTEMPTS - 1 or not rereadable or time.monotonic() >= limit:
                    break
        job_cached = getattr(getattr(self, "_local", None), "inventory", None)
        if job_cached is not None:
            cached = job_cached
        if cached is not None and self._readonly_read_failure(device):
            return cached
        raise failure

    def _outbox_guard(self, action):
        if not self._access():
            return False
        if getattr(self._local, "mode", None) == "drain":
            return action.operation == "read_sent"
        context = self._local.context
        if type(context) is MeshReceiveContext:
            operation = self._local.operation
            if operation == "background_dispatch" and action.operation in ("dispatch", "retry"):
                # A retry decision re-queues a row whose fragment already reached
                # the wire, so it carries the same weight as the dispatch itself:
                # the peer card must still be the live consented one.
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

    def _submit(self, operation, *, deadline=None):
        """Submit a producer job, optionally waiting out a live producer fence.

        SerialScheduler.submit already refuses, atomically under its own
        condition, whenever a producer fence is live, so no second runtime lock
        is needed here - and holding one across a fence starved the background
        worker (unit A journal 2026-09-13 10:44-10:48). A live fence is the
        normal consequence of an owner-session mailbox read, so a caller with a
        deadline of its own waits for that read to finish instead of failing the
        owner's action outright.
        """
        while True:
            try:
                return self.coordinator.submit(operation)
            except SerialOwnerError as error:
                code = error.args[0] if error.args else None
                if code not in ADMISSION_REFUSAL_CODES or deadline is None:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(SUBMIT_RETRY_SECONDS, remaining))

    @contextmanager
    def _fence(self):
        # The coordinator's own fence is the mutual exclusion: producer_fence()
        # waits for every submitted job to finish and marks admission fenced
        # while the lease is live, and submit() refuses in that window. This
        # runtime must NOT hold a lock of its own across the fenced body: the
        # body performs backend store round trips, so an owner-session mailbox
        # read held it long enough to starve the background job submission and
        # retire the receive worker (unit A journal 2026-09-13 10:44-10:48,
        # MeshRuntimeError from _submit with wire_frames 0 every ~26 s).
        deadline = time.monotonic() + self.config.operation_timeout
        remaining = deadline - time.monotonic()
        _require(remaining > 0)
        # A read fence refuses when the serial owner is busy; it must not contain
        # the runtime for it. Containment is the drain's rule (MeshRuntime.close
        # takes the default), and one slow background job must not turn every
        # route into a 503 until a restart.
        with self.coordinator.producer_fence(timeout=remaining, contain_on_timeout=False) as lease:
            yield lease

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
        progress = self._run(context, "queue_message", queue)
        if getattr(progress, "state", None) == "queued":
            # The next dispatch wake must find this row without a journal scan:
            # that wake is what the owner's send is waiting on.
            self._queued_rows().add(message_id)
        return progress

    def ready(self, context):
        def check():
            self.store.storage.snapshot()
            return self._access()
        # Readiness is storage + authorization, not a radio round trip. Running it
        # through the serial coordinator made every status check race the receive
        # poller: a check queued behind a background job timed out, set the
        # permanent uncertainty flag and left messaging not_ready until restart.
        return self._read(context, "status", check)

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
            if receipt:
                return target.dispatch_one(identifier, device=device, radio_timestamp=radio_timestamp,
                                           access_check=self._access)
            # This operation already refreshed the inventory; hand that same one
            # to the row, so one fragment costs one contact read and not two.
            return target.dispatch_one(identifier, device=device, radio_timestamp=radio_timestamp,
                                       access_check=self._access,
                                       inventory=self._local.inventory)
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
            if not self._capture_deadline_open():
                return state
            storage = getattr(self._local, "receive_storage", self._storage_lifecycle)
            _require(storage() is True)
            job = getattr(self._local, "job", None)
            if job is not None:
                job.radio_action.set()
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

    @contextmanager
    def _airtime_reservation(self, device, *, frames=1):
        """Two-phase duty-cycle reservation around one transmission attempt.

        The check runs before the attempt and the estimate is committed after it
        (including when it raised, because a fragment that reached the wire has
        already spent airtime). A configured gate fails closed until the first
        firmware counter reading has been cached by a health() call, because a
        duty-cycle limit must not transmit on trust. Without a configured policy
        this is a no-op, so an unconfigured unit behaves exactly as before.
        Denials are pacing, not failure: the caller stops this wake and the
        worker retries on its normal cadence.
        """
        ledger = getattr(self, "_airtime", None)
        if ledger is None:
            yield True
            return
        sample = getattr(device, "airtime_sample", None)
        if sample is not None:
            ledger.observe(sample)
        if not ledger.allow(now=time.monotonic(), frames=frames):
            count = getattr(self, "_airtime_deferrals", 0) + 1
            self._airtime_deferrals = count
            if count <= 2 or count % 32 == 0:
                _log_airtime_deferral(ledger, count)
            yield False
            return
        try:
            yield True
        finally:
            ledger.spend(frames=frames)

    def _dispatch_pending(self, device):
        # Retryable unknowns first: a row requeued here is picked up by the
        # ordinary dispatch pass below on this same wake.
        self._reconcile_pending()
        # The epoch check is in-process and free; every store call below is not,
        # and after an owner session rotates it is *denied*, not slow. A wake
        # that found out by calling the store stayed inside its job until the
        # fence drain timed out and contained the whole runtime (unit B journal
        # 2026-09-18 10:18 and unit A 18:19: background_dispatch
        # WorkflowUnavailable access_denied authorize_context, then every route
        # 503). Failing here instead retires the worker in milliseconds, and the
        # next authenticated request restarts it under the current epoch.
        _require(self._access() is True)
        if self._public_gateway is not None:
            with self._airtime_reservation(device) as allowed:
                if not allowed:
                    return
                self._public_gateway.dispatch(device, access_check=self._access)
            return
        if self._public_mesh is not None:
            probe = getattr(self._public_mesh, "contact_probe", None)
            if probe is not None and probe.active():
                for _ in range(PUBLIC_BURST):
                    with self._airtime_reservation(device) as allowed:
                        if not allowed:
                            return
                        sent = probe.dispatch_one(device=device, access_check=self._access,
                                                  inventory=self._local.inventory)
                    if not sent:
                        break
                return
            for request_id in self._public_mesh.pending_ids():
                progress, _ = self._public_mesh.inspect(request_id, access_check=self._access)
                if progress.state == "queued":
                    for _ in range(PUBLIC_BURST):
                        with self._airtime_reservation(device) as allowed:
                            if not allowed:
                                return
                            progress = self._public_mesh.dispatch_one(request_id, device=device,
                                access_check=self._access, inventory=self._local.inventory)
                        if progress.state != "queued":
                            break
                    return
            if self._public_mesh.active():
                return
        # Rows this runtime queued, or already found, dispatch from memory first.
        # The journal scan below costs the whole wake on this device - every page
        # is a snapshot pair plus a list call - and the owner's own send is
        # blocked on the same serial owner for as long as the wake runs (unit A
        # journal 2026-09-18 17:22-17:35: the wake died on its budget while the
        # send that created the row was answered 503).
        for message_id in tuple(self._queued_rows()):
            try:
                progress = self._dispatch_row(device, message_id)
            except Exception:
                self._queued_rows().discard(message_id)
                continue
            if progress is None or progress.state != "queued":
                self._queued_rows().discard(message_id)
            return
        if time.monotonic() < getattr(self, "_next_scan_at", 0.0):
            return
        self._next_scan_at = time.monotonic() + DISPATCH_SCAN_SECONDS
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
                with self._airtime_reservation(device) as allowed:
                    if not allowed:
                        return
                    self.receipts.dispatch_one(approved["receipt_id"], device=device,
                        radio_timestamp=int(time.time()), access_check=self._access)
                return
            if row["kind"] != "mesh_outbound" or row["payload"].get("body", {}).get("schema") != OUTBOX_SCHEMA:
                continue
            body = self.outbox._verified(row)
            if body["state"] == "dispatching":
                # Adopt a durable 'dispatching' row the construction scan let
                # through (in-window unknowns are retryable, owner decision
                # 2026-09-13). Recording it here hands it to the retry driver on
                # this same wake; a lapsed window is never adopted and keeps its
                # own fail-closed handling.
                if body["approved"]["issued_at"] <= int(time.time()) < body["approved"]["expires_at"]:
                    self.outbox.track_unknown(body["message_id"], body["approved"]["expires_at"])
                continue
            if body["state"] != "queued" or not body["approved"]["issued_at"] <= int(time.time()) < body["approved"]["expires_at"]:
                continue
            self._queued_rows().add(body["message_id"])
            progress = self._dispatch_row(device, body["message_id"])
            if progress is None or progress.state != "queued":
                self._queued_rows().discard(body["message_id"])
            return
        self._handover_custody(device)

    def _queued_rows(self):
        """Message ids known to be queued, so a wake need not scan the journal."""
        state = getattr(self, "_queued_ids", None)
        if state is None:
            state = self._queued_ids = set()
        return state

    def _dispatch_wake_needed(self):
        """True when a dispatch wake could put something on the wire.

        The worker otherwise opens a capture lease and pays the whole access
        prologue every cycle even when the durable store holds nothing to send.
        A wake with nothing to dispatch cannot write a frame, so that job only
        holds the serial device while it runs - measured on unit B 2026-09-19:
        the job spent its whole 10 s budget in the prologue and in the queue,
        and the owner's own request was refused behind it. Skipping the empty
        job cannot lose a row: the queued set and the unresolved set are
        consulted first, and the throttled journal scan keeps its own cadence,
        so the first wake after a restart and every scan interval still run.
        """
        if self._queued_rows():
            return True
        outbox = getattr(self, "outbox", None)
        if outbox is not None and outbox.unresolved():
            return True
        if getattr(self, "_public_gateway", None) is not None or getattr(self, "_public_mesh", None) is not None:
            # The public paths keep their own pending work that only a real job
            # can drain; they are not covered by the queued/unresolved sets.
            return True
        return time.monotonic() >= getattr(self, "_next_scan_at", 0.0)

    def _dispatch_row(self, device, message_id):
        """Send up to DISPATCH_BURST fragments of one queued row on this wake.

        Each fragment is still its own CAS at its own revision and still marks the
        row unknown on its own failure, so the retry cursor and the window
        semantics are unchanged - this only stops the wake pacing from being the
        limit. Same single inventory read as the fenced path: the wake refreshed
        it at the top of this job and each fragment spends it here.
        """
        deadline = getattr(self._local, "background_deadline", None)
        for _ in range(DISPATCH_BURST):
            if deadline is not None and time.monotonic() >= deadline - DISPATCH_RESERVE_SECONDS:
                return None
            with self._airtime_reservation(device) as allowed:
                if not allowed:
                    # Deferral uses the same "not now" signal as the deadline:
                    # the row stays queued in the store and the bounded journal
                    # scan re-adds it to the in-memory set on its next pass.
                    return None
                progress = self.outbox.dispatch_one(message_id, device=device,
                                                    radio_timestamp=int(time.time()),
                                                    access_check=self._access,
                                                    inventory=self._local.inventory)
            # "queued" means there is another fragment still to send; anything
            # else (awaiting_receipt, delivered, ...) ends this message's turn.
            if progress.state != "queued" or progress.next_fragment >= progress.fragment_count:
                return progress
        return progress

    def _handover_custody(self, device):
        """Hand one held deposit to a present recipient, bounded per wake.

        Presence is a contact in the inventory this wake just read, nothing
        stronger. The inner sealed envelope is sent exactly as its original
        sender would have sent it, so the recipient's existing signature check
        and (sender, message_id) dedupe apply unchanged. Sending fragments is
        not delivery: the copy stays held until its TTL or an explicit
        confirmation, and the recipient's own receipt goes to the original
        sender.
        """
        custodian = getattr(self, "_custodian", None)
        inventory = getattr(self._local, "inventory", None)
        if custodian is None or inventory is None or self._access() is not True:
            return False
        now = int(time.time())
        deadline = getattr(self._local, "background_deadline", None)
        for contact in inventory.contacts:
            prefix = contact.public_key[:6]
            try:
                pending = custodian.handover_for(prefix, now=now, min_interval=HANDOVER_RETRY_SECONDS)
            except CustodianError:
                return False
            if not pending:
                continue
            try:
                target = inventory.unique_prefix(contact.public_key)
            except Exception:
                continue
            deposit, next_index, _ = pending[0]
            try:
                fragments = split_envelope(deposit.envelope_wire.encode("ascii"))
            except Exception:
                return False
            index = next_index
            while index < len(fragments) and index - next_index < HANDOVER_BURST:
                if deadline is not None and time.monotonic() >= deadline - DISPATCH_RESERVE_SECONDS:
                    break
                with self._airtime_reservation(device) as allowed:
                    if not allowed:
                        return True
                    device.send_text(recipient_prefix=target, text=fragments[index],
                                     timestamp=int(time.time()), authorize=self._access)
                index += 1
            try:
                custodian.note_handover(prefix, deposit.message_digest, next_index=index,
                                        complete=index >= len(fragments), now=int(time.time()))
            except CustodianError:
                return False
            return True
        return False

    def _retry_state(self):
        state = getattr(self, "_retry_pending", None)
        if state is None:
            state = self._retry_pending = {"next": {}, "attempts": {}}
        return state

    def _await_abandoned(self, future, timeout=ABANDONED_JOB_SECONDS):
        """Bounded wait for a timed-out job to observe the fence and unwind.

        Returns False if a radio call from the abandoned job could still overlap
        the retry decision that follows it, in which case the worker retires
        fail-closed exactly as before.
        """
        if future is None or future.done():
            return True
        try:
            future.result(timeout=timeout)
        except Exception:
            return future.done()
        return True

    def _outbox_recoverable(self):
        """True when an unknown outbox outcome is still retryable inside its window."""
        outbox = getattr(self, "outbox", None)
        if outbox is None:
            return False
        return bool(outbox.unresolved()) and not outbox.latching_unknown()

    def _recovery_allowed(self, written):
        """One recovery-continue per distinct radio-progress point.

        The worker keeps living after an unknown dispatch outcome only while the
        retry decision has a named, in-window row *and* the radio has moved since
        the last recovery. A wedged transport that writes nothing new therefore
        retires fail-closed instead of spinning.
        """
        if not self._outbox_recoverable() or type(written) is not int:
            return False
        if getattr(self, "_recovered_at", None) == written:
            return False
        self._recovered_at = written
        return True

    def _reconcile_pending(self):
        """Requeue message rows a lost acknowledgement left in 'dispatching'.

        Owner decision 2026-09-13: an unknown dispatch outcome is retryable
        inside the row's own signed window instead of parking the unit at
        not_ready until a human restarts it. The audited
        ``MeshOutbox.retry_decision`` CAS is the only way back to 'queued': same
        wire, message id and fragment cursor, consent re-verified, one attempt
        per row per wake with backoff. A row whose window has lapsed is left
        exactly where it is - visible, unresolved, and still refusing a clean
        close - and a retry_dispatch (radio) is attempted once the row is queued.
        """
        outbox = getattr(self, "outbox", None)
        if outbox is None or not outbox.unresolved():
            return False
        if self._access() is not True:
            return False
        state = self._retry_state()
        wall, monotonic = int(time.time()), time.monotonic()
        # The unresolved ids are already known, so each row is loaded by key
        # instead of scanning the journal: that scan was the whole background
        # budget on this device (unit B journal 2026-09-18 10:11: one dispatch
        # wake died with wire_frames 0 inside it) and reconciliation is the
        # first thing every dispatch wake does.
        for identifier in tuple(outbox.unresolved()):
            try:
                body = self.outbox._load(identifier, self._access)
            except Exception:
                continue
            approved = body["approved"]
            if body["state"] != "dispatching":
                # The durable row answered the lost write by observation: a
                # landed CAS is queued again, or the row has already moved past
                # dispatch. Either way no retry decision is owed, and keeping
                # the row-scoped unknown would latch the unit when its window
                # lapses even though nothing is unresolved (unit B, 2026-09-19).
                outbox.resolve_unknown(identifier)
                state["next"].pop(identifier, None)
                state["attempts"].pop(identifier, None)
                continue
            if not approved["issued_at"] <= wall < approved["expires_at"]:
                continue
            due = state["next"].setdefault(identifier, monotonic + RETRY_BACKOFF_SECONDS[0])
            if monotonic < due:
                continue
            attempts = state["attempts"].get(identifier, 0)
            try:
                self.outbox.retry_decision(identifier, expected_revision=body["revision"],
                                           decision_binding=AUTO_RETRY_BINDING, access_check=self._access)
            except Exception as error:
                attempts += 1
                state["attempts"][identifier] = attempts
                state["next"][identifier] = monotonic + RETRY_BACKOFF_SECONDS[
                    min(attempts, len(RETRY_BACKOFF_SECONDS) - 1)]
                _log_background_failure(error, "background_retry", True, 0)
                continue
            state["next"].pop(identifier, None)
            state["attempts"].pop(identifier, None)
            self._queued_rows().add(identifier)
        return False

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
            def invoke(device, operation, job):
                _require(not stop.is_set() and not self._stop.is_set())
                if operation not in permitted_operations():
                    return
                with grant_factory(operation) as (context, storage_check):
                    _require(type(context) is MeshReceiveContext and callable(storage_check))
                    self._local.receive_stop = stop
                    self._local.job = job
                    self._local.background_deadline = time.monotonic() + job_timeout
                    self._local.capture_reserve = min(CAPTURE_RESERVE_SECONDS, job_timeout / 2)
                    with self._context(context, operation):
                        self._local.receive_storage = storage_check
                        public_active = self._public_active()
                        if public_active or gateway is not None:
                            self._local.public_job_deadline = time.monotonic() + job_timeout
                        # This read is now the *only* contact read a dispatched
                        # fragment pays for, so it takes the same bounded re-read
                        # the fenced path uses: a read-only stall the port
                        # resynchronised from must not cost the whole wake. A
                        # wake reuses a recent read entirely instead of paying
                        # the serial round trip again: the owner's own send waits
                        # on this wake, and the table only changes on an import.
                        self._local.inventory = self._inventory(device, self._access,
                            fresh=INVENTORY_WAKE_FRESH_SECONDS)
                        if operation == "receive":
                            for _ in range(PUBLIC_BURST if public_active else 1):
                                if self._poll(device) == "idle":
                                    break
                        else:
                            # Do NOT set the destructive-action marker up front here.
                            # The dispatch layers set their own uncertainty flag only
                            # after a fragment actually reached the wire (rf_started in
                            # mesh_outbox.dispatch_one), so a timeout during the
                            # read-only inventory read that starts this path must stay
                            # classifiable as pre-wire; otherwise one slow contact read
                            # latches the whole runtime until a process restart.
                            self._dispatch_pending(device)
            def permitted_operations():
                choices = operations()
                _require(type(choices) is tuple and len(choices) <= 2
                         and all(type(choice) is str and choice in ("receive", "background_dispatch") for choice in choices)
                         and len(set(choices)) == len(choices))
                return choices
            def worker():
                nonlocal stop
                index = 0
                backoff = BACKGROUND_RETRY_MIN_SECONDS
                refusals = 0
                while not stop.is_set() and not self._stop.is_set():
                    future = None
                    operation = None
                    job = _BackgroundJob()
                    written = self._radio_written()
                    try:
                        choices = permitted_operations()
                        if not choices:
                            stop.wait(interval)
                            continue
                        operation = choices[index % len(choices)]
                        # An idle dispatch wake cannot send anything: it would
                        # only hold the serial device for its whole budget (see
                        # _dispatch_wake_needed). Let the scan cadence, the
                        # queued set or the unresolved set re-arm it.
                        if operation == "background_dispatch" and not self._dispatch_wake_needed():
                            index += 1
                            stop.wait(interval)
                            continue
                        future = self._submit(lambda device: invoke(device, operation, job))
                        future.result(timeout=job_timeout)
                    except Exception as error:
                        current = self._radio_written()
                        refusal = (isinstance(error, SerialOwnerError)
                                   and (error.args[0] if error.args else None) in ADMISSION_REFUSAL_CODES)
                        # A refusal admits nothing. A timeout is only pre-wire when
                        # the job's own destructive-action marker is unset and the
                        # transport is intact; an unacknowledged capture lease stays
                        # uncertain by design and must keep blocking drain (see
                        # test_unknown_supervisor_lease_outcome_blocks_clean_close).
                        pre_wire = ((refusal or isinstance(error, FutureTimeout))
                                    and not job.radio_action.is_set() and not self._radio_poisoned())
                        refusals = refusals + 1 if refusal else 0
                        # A fence that an owner-session read holds refuses every
                        # retry for its whole body, so log the first few and then
                        # one in thirty-two rather than a line every half second.
                        if not refusal or refusals <= 2 or refusals % 32 == 0:
                            _log_background_failure(error, operation, pre_wire,
                                None if type(written) is not int or type(current) is not int else current - written,
                                job.access_denied, job.fenced_rows)
                        if (refusal and pre_wire and not self._closed
                                and not self._stop.is_set() and self.admission_safe()):
                            # A refusal admitted nothing at all - the coordinator
                            # rejected the job before it was queued - so there is no
                            # radio or firmware state to resolve and nothing to
                            # overlap. Wait for the fence to lift and try again
                            # instead of stopping the receive loop.
                            stop.wait(backoff)
                            backoff = min(backoff * 2, BACKGROUND_RETRY_MAX_SECONDS)
                            continue
                        # Retire this worker before classifying the failure: the
                        # in-flight job observes the stop flag at its next access
                        # check and cannot start a new destructive capture.
                        stop.set()
                        if future is not None:
                            future.cancel()
                        # A capture that reached the destructive dequeue owns the
                        # radio until the reviewed recovery: unchanged, and latched
                        # immediately so that state is never ambiguous.
                        capture_unknown = (job.radio_action.is_set() and not pre_wire
                                           and isinstance(error, (FutureTimeout, MeshLeaseUncertain)))
                        # A dispatch failure is different evidence. The outbox
                        # records the exact row and its own window as the job
                        # unwinds (the dispatch path deliberately does not set the
                        # job marker), so wait for that record before deciding:
                        # owner decision 2026-09-13 makes a fragment that reached
                        # the wire and was never acknowledged retryable inside the
                        # window, so the unit must not park at not_ready until a
                        # human restarts it. Everything else keeps the old latch.
                        recovering = (not capture_unknown and self._await_abandoned(future)
                                      and self._recovery_allowed(current) and not self._closed
                                      and not self._stop.is_set() and not self._receive_pausing
                                      and self.admission_safe())
                        if recovering:
                            with self._condition:
                                self._receive_stop = threading.Event()
                            stop = self._receive_stop
                            refusals = 0
                            backoff = BACKGROUND_RETRY_MIN_SECONDS
                            stop.wait(min(BACKGROUND_RETRY_MIN_SECONDS, interval))
                            continue
                        if capture_unknown or (isinstance(error, (FutureTimeout, MeshLeaseUncertain))
                                               and not pre_wire):
                            self._uncertain = True
                        self.admission_safe()
                        # Break, not return: the cleanup below is what retires
                        # this worker's own state, and this is the path a
                        # rotated capture epoch takes.
                        break
                    index += 1
                    backoff = BACKGROUND_RETRY_MIN_SECONDS
                    self._recovered_at = None
                    public_active = self._public_active()
                    stop.wait(gateway.poll_interval if gateway is not None else
                              min(interval, PUBLIC_POLL_INTERVAL) if public_active else interval)
                # This worker is done. Its own thread reference stays for the
                # observers that join it (a dead thread joins instantly, and
                # restart_receiving clears it), but the pause flag must not
                # outlive it: on 2026-09-18 a join that could not observe a
                # retiring worker left the runtime pausing, so every later
                # start was refused and every route answered 503.
                with self._condition:
                    if self._poller is threading.current_thread():
                        self._receive_pausing = False
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

    def restart_receiving(self, grant_factory, operations, *, interval=1, timeout=3):
        """Re-admit background work after the previous worker retired.

        An owner session rotates the storage epoch, which denies every store
        call a capture admitted under the old one was authorized for; the wake
        retires by design. This is the off-request-path way back: a supervisor
        thread calls it, never a request, and unlike pause_receiving it never
        marks the runtime as pausing - a failed attempt leaves the state exactly
        as it was and the caller may retry. The previous thread must already be
        gone and the serial owner must be provably free, because a job that
        could not be observed unwinding still owns its fence; the probe is the
        same producer-fence round trip a graceful pause uses. The first start
        has nothing to fence, so it skips the probe.
        """
        _require(callable(grant_factory) and callable(operations))
        _require(type(interval) in (int, float) and math.isfinite(interval) and 0.2 <= interval <= 30)
        _require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 30)
        with self._condition:
            if self._stop.is_set() or self._closed:
                return False
            poller = self._poller
            if poller is not None and poller.is_alive():
                return True
            if not self.admission_safe():
                return False
            first = poller is None and not self._background_receive
            if poller is not None:
                self._poller = None
            self._receive_pausing = False
        if not first:
            try:
                self.coordinator.submit(lambda device: None).result(timeout=timeout)
            except Exception:
                return False
        try:
            self.start_receiving(grant_factory, operations, interval=interval)
            return True
        except Exception:
            poller = self._poller
            return poller is not None and poller.is_alive()

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
        denial = getattr(self, "_denial", None)
        if denial is not None:
            denial.reason = None
        try:
            with self._context(context, operation):
                return callback()
        except Exception as error:
            # A refused read leaves a trace of its own: without this the journal
            # carried neither the operation, the conjunct that refused it, nor
            # the row whose unknown outcome fences admission (unit A,
            # 2026-09-22 - every mailbox read answered 503 while the cause was
            # hidden behind `raise MeshRuntimeError() from None`).
            self.admission_safe()
            fence = self.admission_fence() or {}
            _log_operation_failure(error, operation, access_denied=getattr(denial, "reason", None),
                                   fenced_rows=tuple(fence.get("rows") or ()))
            raise MeshRuntimeError() from None
        finally:
            with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    def list_messages(self, context, box, *, limit=20):
        page = self._read(context, "list_messages", lambda: self.mailbox.list_messages(box, limit=limit, access_check=self._access))
        if box == "outbox":
            # The dispatch wake finds rows from memory, and a row queued by an
            # earlier process is otherwise only discoverable by a journal scan
            # that costs more than the whole wake (unit A, 2026-09-21: the scan
            # died on the 10 s job budget and the message never left the queue).
            # An outbox read is the owner looking at exactly those rows, so it
            # seeds the set the wake dispatches from.
            for item in page.items:
                if item.workflow_state == "queued":
                    self._queued_rows().add(item.local_id)
        return page

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
