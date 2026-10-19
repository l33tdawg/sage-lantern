"""Durable private receipt producer; no worker, port opening, or automatic retry.

The caller owns the exclusive MeshCoreClient and its underlying driver, including
cross-process fencing. policy(action) is an authoritative producer callback, not
an HTTP boolean: it returns ReceiptPermit bound to that exact action. For read,
privacy_binding proves opt-in and render_ack_binding proves the authenticated
render ACK for this original under the current session. Historical inbox proof
alone grants neither send nor read authority. Callbacks must use bounded IO.

Two deterministic per-original slots, one per receipt kind, hold actual sealed
receipt envelopes and signed progress in the existing encrypted workflow store.
No private message text is copied or encoded into an unrelated message queue.
Serial acceptance is not remote delivery. Unknown dispatch remains dispatching;
retry_decision requires an operator policy that fences the original worker.
close joins this instance only, never drains another process or a radio queue.
"""

from dataclasses import dataclass
from functools import wraps
import hashlib
import json
import threading
import time
import uuid

from nacl.signing import SigningKey

from .mesh_contacts import VerifiedMeshContact
from .mesh_envelope import Peer, seal_delivery_receipt, seal_read_receipt
from .mesh_fragments import split_envelope
from .mesh_inbox import MeshInbox
from .meshcore_transport import MeshCoreClient, SendReceipt
from .message_contacts import RecipientID
from .workflow_store import WorkflowStore, WorkflowNotFound, WorkflowOutcomeUnknown, MAX_REVISION


SCHEMA = "sage.lantern.mesh-receipt-sender.v1"
NAMESPACE = uuid.UUID("9876397c-82e3-4d0b-b0a0-ea1a7dd94781")


class ReceiptSenderError(RuntimeError):
    def __init__(self):
        super().__init__("mesh_receipt_sender_unavailable")


def _check(value):
    if value is not True:
        raise ReceiptSenderError()


def _integer(value, lower, upper):
    _check(type(value) is int and lower <= value <= upper)


def _uuid(value):
    _check(type(value) is str and len(value) == 36)
    parsed = uuid.UUID(value)
    _check(parsed.int != 0 and str(parsed) == value)
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def _binding(value):
    _check(type(value) is str and 1 <= len(value) <= 128 and all(char.isascii() and (char.isalnum() or char in "._:-") for char in value))


@dataclass(frozen=True, repr=False)
class ReceiptAction:
    operation: str
    local_id: str
    kind: str
    original_sender: Peer
    original_id: str
    original_digest: str
    receipt_id: str
    radio_public_key: bytes
    decision_binding: str = ""


@dataclass(frozen=True, repr=False)
class ReceiptPermit:
    action: ReceiptAction
    privacy_binding: str
    render_ack_binding: str = ""


@dataclass(frozen=True)
class ReceiptProgress:
    receipt_id: str
    revision: int
    state: str
    next_fragment: int
    fragment_count: int


def _admitted(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self._condition:
            _check(not self._closed and not self._active)
            self._active = True
        try:
            return method(self, *args, **kwargs)
        except WorkflowOutcomeUnknown:
            self._uncertain = True
            raise ReceiptSenderError() from None
        except Exception:
            raise ReceiptSenderError() from None
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()
    return invoke


class MeshReceiptSender:
    def __init__(self, store, identity, *, inbox, sender, guard, policy, verify_contact, clock=lambda: int(time.time())):
        try:
            _check(type(store) is WorkflowStore and type(inbox) is MeshInbox and type(sender) is Peer)
            _check(inbox._store is store and inbox._recipient == sender)
            _check(isinstance(identity._signing_key, SigningKey) and bytes(identity._signing_key.verify_key) == sender.public_key)
            _check(store.storage.actor == RecipientID(sender.chain_id, sender.public_key.hex()))
            _check(all(callable(callback) for callback in (guard, policy, verify_contact, clock)))
        except Exception:
            raise ReceiptSenderError() from None
        self.store, self.identity, self.inbox, self.sender = store, identity, inbox, sender
        self.guard, self.policy, self.verify_contact, self.clock = guard, policy, verify_contact, clock
        self._condition = threading.Condition()
        self._active = self._closed = self._uncertain = False
        self._last = None

    def close(self, timeout=0):
        _check(type(timeout) in (int, float) and 0 <= timeout <= 30)
        with self._condition:
            self._closed = True
            joined = self._condition.wait_for(lambda: not self._active, timeout)
            return joined and not self._uncertain

    def _now(self):
        now = self.clock()
        _integer(now, 1, 2**53-1)
        _check(self._last is None or now >= self._last)
        self._last = now
        return now

    def _live(self, action, access_check):
        _check(not self._closed and access_check() is True and self.guard(action) is True)
        return True

    def _slot(self, local_id, kind):
        _uuid(local_id)
        _check(kind in ("delivery", "read") and type(kind) is str)
        return str(uuid.uuid5(NAMESPACE, _json([self.sender.to_dict(), local_id, kind]).decode()))

    def _action(self, approved, operation, decision=""):
        peer = approved["recipient"]
        return ReceiptAction(operation, approved["local_id"], approved["kind"],
                             Peer(peer["chain_id"], bytes.fromhex(peer["public_key"])),
                             approved["original_id"], approved["original_digest"], approved["receipt_id"],
                             bytes.fromhex(approved["radio_public_key"]), decision)

    def _authorize(self, approved, operation, access_check, decision=""):
        action = self._action(approved, operation, decision)
        self._live(action, access_check)
        permit = self.policy(action)
        _check(type(permit) is ReceiptPermit and permit.action == action)
        _binding(permit.privacy_binding)
        if action.kind == "read":
            _binding(permit.render_ack_binding)
        else:
            _check(permit.render_ack_binding == "")
        if "privacy_binding" in approved:
            _check(permit.privacy_binding == approved["privacy_binding"] and permit.render_ack_binding == approved["render_ack_binding"])
        contact = self.verify_contact(action)
        now = self._now()
        _check(type(contact) is VerifiedMeshContact and contact.recipient == RecipientID(action.original_sender.chain_id, action.original_sender.public_key.hex())
               and contact.radio_public_key == action.radio_public_key.hex() and contact.findable is True
               and contact.messaging_allowed is True and contact.issued_at <= now < contact.expires_at)
        self._live(action, access_check)
        return permit

    def _proof(self, body, domain):
        signature = self.identity._signing_key.sign(domain + _json(body)).signature.hex()
        return dict(body=body, signature=signature)

    def _verify(self, proof, domain):
        _check(type(proof) is dict and set(proof) == {"body", "signature"})
        signature = proof["signature"]
        _check(type(signature) is str and len(signature) == 128 and bytes.fromhex(signature).hex() == signature)
        self.identity._signing_key.verify_key.verify(domain + _json(proof["body"]), bytes.fromhex(signature))
        return proof["body"]

    def _verified(self, record):
        body = self._verify(record["payload"], b"LANTERN-RECEIPT-PROGRESS\x00")
        _check(type(body) is dict and set(body) == {"schema", "revision", "approval", "state", "next_fragment", "timestamp", "attempts"}
               and body["schema"] == SCHEMA and body["revision"] == record["revision"] and record["kind"] == "mesh_outbound")
        _integer(body["revision"], 1, MAX_REVISION)
        approved = self._verify(body["approval"], b"LANTERN-RECEIPT-APPROVAL\x00")
        _check(type(approved) is dict and set(approved) == {"sender", "recipient", "local_id", "kind", "original_id", "original_digest", "receipt_id",
               "radio_public_key", "issued_at", "expires_at", "privacy_binding", "render_ack_binding", "wire", "wire_hash"})
        _check(approved["sender"] == self.sender.to_dict() and approved["receipt_id"] == record["record_id"]
               and self._slot(approved["local_id"], approved["kind"]) == approved["receipt_id"])
        _uuid(approved["original_id"])
        _binding(approved["privacy_binding"])
        if approved["kind"] == "read":
            _binding(approved["render_ack_binding"])
        else:
            _check(approved["render_ack_binding"] == "")
        self._action(approved, "inspect")
        for field in ("original_digest", "radio_public_key", "wire_hash"):
            value = approved[field]
            _check(type(value) is str and len(value) == 64 and bytes.fromhex(value).hex() == value)
        _integer(approved["issued_at"], 1, 2**53-1)
        _integer(approved["expires_at"], approved["issued_at"]+1, approved["issued_at"]+3600)
        wire = approved["wire"].encode("ascii")
        _check(hashlib.sha256(wire).hexdigest() == approved["wire_hash"])
        fragments = split_envelope(wire)
        _integer(body["next_fragment"], 0, len(fragments))
        _integer(body["attempts"], 0, 256)
        _check(body["state"] in ("queued", "dispatching", "serial_accepted"))
        if body["timestamp"] is not None:
            _integer(body["timestamp"], 1, 0xffffffff)
        if body["state"] == "serial_accepted":
            _check(body["next_fragment"] == len(fragments) and body["timestamp"] is None)
        else:
            _check(body["next_fragment"] < len(fragments))
        if body["state"] == "dispatching":
            _check(body["timestamp"] is not None and body["attempts"] > 0)
        return body, approved

    def _load(self, identifier, access_check):
        _uuid(identifier)
        return self._verified(self.store.get(identifier, kind="mesh_outbound", access_check=access_check))

    def _put(self, identifier, body, revision, access_check):
        updated = {**body, "revision": revision+1}
        proof = self._proof(updated, b"LANTERN-RECEIPT-PROGRESS\x00")
        self._verified(dict(payload=proof, record_id=identifier, revision=revision+1, kind="mesh_outbound"))
        return self._verified(self.store.put(identifier, "mesh_outbound", revision, proof, access_check=access_check))

    def _progress(self, body, approved):
        return ReceiptProgress(approved["receipt_id"], body["revision"], body["state"], body["next_fragment"],
                               len(split_envelope(approved["wire"].encode("ascii"))))

    @_admitted
    def queue(self, *, local_id, kind, radio_public_key, issued_at, expires_at, access_check):
        identifier = self._slot(local_id, kind)
        _check(type(radio_public_key) is bytes and len(radio_public_key) == 32)
        _integer(issued_at, 1, 2**53-1)
        _integer(expires_at, issued_at+1, issued_at+3600)
        _check(not self._closed and access_check() is True)
        original = self.inbox.historical_read(local_id)
        _check(original.kind == "message" and original.recipient == self.sender and issued_at >= original.issued_at)
        approved = dict(sender=self.sender.to_dict(), recipient=original.sender.to_dict(), local_id=local_id, kind=kind,
                        original_id=original.message_id, original_digest=original.digest, receipt_id=identifier,
                        radio_public_key=radio_public_key.hex(), issued_at=issued_at, expires_at=expires_at)
        permit = self._authorize(approved, "queue", access_check)
        try:
            body, existing = self._load(identifier, access_check)
        except WorkflowNotFound:
            pass
        else:
            _check(all(existing[key] == value for key, value in approved.items()))
            self._authorize(existing, "queue", access_check)
            return self._progress(body, existing)
        _check(issued_at <= self._now() < expires_at)
        permit = self._authorize(approved, "queue", access_check)
        arguments = dict(sender=self.sender, recipient=original.sender, message_id=identifier,
                         original_id=original.message_id, original_digest=original.digest, issued_at=issued_at, expires_at=expires_at)
        if kind == "read":
            sealed = seal_read_receipt(self.identity._signing_key, explicit_user_read=True, **arguments)
        else:
            sealed = seal_delivery_receipt(self.identity._signing_key, **arguments)
        approved.update(privacy_binding=permit.privacy_binding, render_ack_binding=permit.render_ack_binding,
                        wire=sealed.wire, wire_hash=hashlib.sha256(sealed.wire.encode("ascii")).hexdigest())
        body = dict(schema=SCHEMA, revision=1, approval=self._proof(approved, b"LANTERN-RECEIPT-APPROVAL\x00"),
                    state="queued", next_fragment=0, timestamp=None, attempts=0)
        def check():
            self._authorize(approved, "queue", access_check)
            return True
        return self._progress(*self._put(identifier, body, 0, check))

    @_admitted
    def inspect(self, receipt_id, *, access_check):
        body, approved = self._load(receipt_id, access_check)
        self._live(self._action(approved, "inspect"), access_check)
        if body["state"] == "dispatching":
            self._uncertain = True
        return self._progress(body, approved)

    @_admitted
    def dispatch_one(self, receipt_id, *, device, radio_timestamp, access_check):
        _check(type(device) is MeshCoreClient)
        _integer(radio_timestamp, 1, 0xffffffff)
        body, approved = self._load(receipt_id, access_check)
        _check(body["state"] == "queued" and body["attempts"] < 256)
        def journal_check():
            _check(approved["issued_at"] <= self._now() < approved["expires_at"])
            return self._live(self._action(approved, "dispatch"), access_check)
        def check():
            _check(approved["issued_at"] <= self._now() < approved["expires_at"])
            self._authorize(approved, "dispatch", access_check)
            return True
        check()
        prefix = device.get_contacts(authorize=check).unique_prefix(bytes.fromhex(approved["radio_public_key"]))
        stamp = body["timestamp"] or radio_timestamp
        # Fresh policy/contact checks bracket each CAS and RF admission. The
        # journal's internal access guards still check the live grant and its
        # own storage epoch without recursively re-reading the contact index.
        body, approved = self._put(receipt_id, {**body, "state":"dispatching", "timestamp":stamp, "attempts":body["attempts"]+1}, body["revision"], journal_check)
        try:
            check()
            fragments = split_envelope(approved["wire"].encode("ascii"))
            result = device.send_text(recipient_prefix=prefix, text=fragments[body["next_fragment"]], timestamp=stamp, authorize=check)
            _check(type(result) is SendReceipt and result.serial_accepted is True and result.delivered is False and result.read is False)
            check()
            index = body["next_fragment"]+1
            body, approved = self._put(receipt_id, {**body, "next_fragment":index, "timestamp":None,
                                  "state":"serial_accepted" if index == len(fragments) else "queued"}, body["revision"], journal_check)
            check()
            return self._progress(body, approved)
        except Exception:
            self._uncertain = True
            raise

    @_admitted
    def retry_decision(self, receipt_id, *, expected_revision, decision_binding, access_check):
        _integer(expected_revision, 1, MAX_REVISION-1)
        _binding(decision_binding)
        body, approved = self._load(receipt_id, access_check)
        _check(body["state"] == "dispatching" and body["revision"] == expected_revision)
        def check():
            _check(approved["issued_at"] <= self._now() < approved["expires_at"])
            self._authorize(approved, "retry", access_check, decision_binding)
            return True
        check()
        return self._progress(*self._put(receipt_id, {**body, "state":"queued"}, expected_revision, check))
