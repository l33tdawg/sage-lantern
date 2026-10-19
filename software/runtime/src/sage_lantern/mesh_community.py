"""Separate volatile UI grants and supervisor-leased messaging grants.

The HTTP broker supplies a captured session/generation guard. Grants expire on
return, including timeouts, and cannot authorize retries or background workers.
Root separately supplies MeshRuntime's activation and storage lifecycle leases.
receive_grant requires a concrete CaptureLease per producer job and an unchanged
typed operator consent snapshot. It never authorizes mailbox disclosure or read
assertions. Delivery assertions require their separate explicit opt-in. The
root daemon, not unprivileged voice, owns this authority.
The root lease factory must use genuine background admission: the original
unlocked-auth CaptureLease acquire route alone cannot grant locked reception.
"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import threading
import uuid

from .mesh_envelope import SCHEMA, Peer
from .mesh_outbox import OutboxAction
from .mesh_receipt_sender import ReceiptAction, ReceiptPermit
from .mesh_runtime import MeshUserContext, MeshReceiveContext, MeshLeaseUncertain
from .storage_supervisor_socket import CaptureLease
from .message_contacts import RecipientID
from .community_messaging import MessagingUnavailable


@dataclass(frozen=True, repr=False)
class MeshRequestAccess:
    epoch: str
    check: object

    def __call__(self):
        try:
            return self.check() is True
        except Exception:
            return False


@dataclass(frozen=True, repr=False)
class MeshReceiveConsent:
    actor: RecipientID
    revision: str
    messaging_enabled: bool
    receive_while_locked: bool
    dispatch_enabled: bool = False
    dispatch_while_locked: bool = False
    delivery_receipts_enabled: bool = False


class MeshLeaseRefused(RuntimeError):
    """Trusted lease factory positively refused before issuing any lease.

    This is a narrow no-acquisition signal, never evidence of safe retirement
    or permission. Other failures, including a lost issued-token return, remain
    uncertain. Callers must not infer it from message text or an empty journal.
    """
    def __init__(self):super().__init__('mesh_lease_refused')


class MeshActionAuthority:
    def __init__(self):
        self._mutex = threading.Lock()
        self._grants = {}

    @staticmethod
    def receive_allowed(consent, actor, operation, unlocked_check):
        if (type(consent) is not MeshReceiveConsent or type(consent.actor) is not RecipientID or consent.actor != actor
                or type(consent.revision) is not str or re.fullmatch(r"[0-9a-f]{64}", consent.revision) is None
                or any(type(value) is not bool for value in (consent.messaging_enabled, consent.receive_while_locked,
                    consent.dispatch_enabled, consent.dispatch_while_locked, consent.delivery_receipts_enabled)) or not consent.messaging_enabled):
            return False
        if operation == "receive":
            return consent.receive_while_locked or unlocked_check() is True
        if operation == "background_dispatch":
            return consent.dispatch_enabled and (consent.dispatch_while_locked or unlocked_check() is True)
        return False

    @contextmanager
    def receive_grant(self, actor, operation, consent_reader, lease_factory, unlocked_check):
        consent = consent_reader()
        if not self.receive_allowed(consent, actor, operation, unlocked_check):
            raise ValueError("mesh_background_unavailable")
        try:
            lease = lease_factory(consent)
        except (MeshLeaseRefused, MessagingUnavailable):
            # A read-only refusal, not an unknown lease outcome: the factory
            # had not acquired a lease or reached the destructive dequeue, so
            # nothing here can be in flight. Flattening it into
            # MeshLeaseUncertain latched the whole runtime until a cold start
            # when the local node's shared replay window answered 503 to the
            # daemon's own storage probe (unit A, 2026-09-22 14:13). The
            # factory's other failures keep the old meaning - the acquire
            # acknowledgment may be the lost one.
            raise
        except Exception:
            raise MeshLeaseUncertain() from None
        if type(lease) is not CaptureLease:
            raise ValueError("mesh_background_unavailable")
        context = MeshReceiveContext(actor, uuid.uuid4().hex)
        def check():
            return (consent_reader() == consent and self.receive_allowed(consent, actor, operation, unlocked_check)
                    and lease.check() is True)
        record = dict(context=context, access=MeshRequestAccess(context.epoch, check),
                      operations=frozenset((operation,)), background=True, consent=consent)
        try:
            if record["access"]() is not True:
                raise ValueError("mesh_background_unavailable")
            with self._mutex:
                self._grants[id(context)] = record
            yield context, lease.check
        finally:
            with self._mutex:
                self._grants.pop(id(context), None)
            try:
                lease.close()
            except Exception:
                raise MeshLeaseUncertain() from None

    @contextmanager
    def grant(self, actor, access, operations, *, approval_binding=None, message=None):
        if type(access) is not MeshRequestAccess or access() is not True:
            raise ValueError("mesh_request_unavailable")
        context = MeshUserContext(actor, access.epoch, approval_binding or uuid.uuid4().hex)
        record = dict(context=context, access=access, operations=frozenset(operations), message=message, wire=None)
        with self._mutex:
            self._grants[id(context)] = record
        try:
            yield context
        finally:
            with self._mutex:
                self._grants.pop(id(context), None)

    def delivery_binding(self, context, operation):
        expected = {"queue": "receive", "dispatch": "background_dispatch"}.get(operation)
        if type(context) is not MeshReceiveContext or expected is None:
            return None
        with self._mutex:
            record = self._grants.get(id(context))
        if (record is None or record["context"] is not context or record.get("background") is not True
                or record["operations"] != frozenset((expected,)) or record["access"]() is not True):
            return None
        consent = record["consent"]
        if consent.delivery_receipts_enabled is not True or consent.dispatch_enabled is not True:
            return None
        with self._mutex:
            if self._grants.get(id(context)) is not record:
                return None
        return hashlib.sha256(json.dumps(asdict(consent), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def receipt_policy(self, context, action):
        if type(action) is not ReceiptAction or action.kind != "delivery" or action.decision_binding:
            return None
        binding = self.delivery_binding(context, action.operation)
        return ReceiptPermit(action, binding) if binding is not None else None

    def authorized(self, context, operation, action):
        with self._mutex:
            record = self._grants.get(id(context))
            if record is None or record["context"] is not context or operation not in record["operations"]:
                return False
        if record["access"]() is not True:
            return False
        with self._mutex:
            if self._grants.get(id(context)) is not record:
                return False
            if action is None:
                return True
            if type(action) is not OutboxAction:
                return False
            if record.get("background") is True:
                return (operation, action.operation) in {
                    ("receive", "receipt"), ("background_dispatch", "dispatch"),
                    ("background_dispatch", "retry")}
            expected = record["message"]
            if expected is None:
                return operation in ("list_messages", "open_message") and action.operation == "read_sent"
            if (action.operation not in {"queue", "dispatch", "read_sent"}
                    or action.message_id != expected["message_id"] or action.recipient != expected["recipient"]
                    or action.radio_public_key != expected["radio_public_key"]
                    or action.content_digest != expected["content_digest"]
                    or action.approval_binding != context.approval_binding
                    or action.consent_binding != expected["consent_binding"] or action.decision_binding):
                return False
            if ((operation, action.operation) not in {("queue_message", "queue"),
                    ("dispatch_message", "dispatch"), ("open_message", "read_sent")}):
                return False
            if record["wire"] is None:
                if action.operation != "queue":
                    return False
                record["wire"] = action.wire_hash
            return action.wire_hash == record["wire"]

    @staticmethod
    def message(actor, *, message_id, recipient, radio_public_key, content, consent_binding, issued_at, expires_at):
        body = dict(schema=SCHEMA, sender=Peer(actor.chain_id, bytes.fromhex(actor.agent_id)).to_dict(),
                    recipient=recipient.to_dict(), message_id=message_id, kind="message", content=content,
                    issued_at=issued_at, expires_at=expires_at, original_id=None, original_digest=None)
        digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        return dict(message_id=message_id, recipient=recipient, radio_public_key=radio_public_key,
                    consent_binding=consent_binding, content_digest=digest)


def deny_receipt(context, action):
    return None
