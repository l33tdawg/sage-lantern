"""Explicit render-ACK/consent authorization for the existing receipt sender.

Opening/listing is never a receipt. begin_render issues only a short-lived opaque
challenge after a verified inbox read. The separately authenticated UI event must
assert both rendered=True and share_read_receipt=True. This proves an authenticated
render assertion, not physical display visibility or that a human read the text.

authorize(session, operation) MUST check the live Root-issued epoch/session binding;
HTTP fields are not an authorization callback. radio_public_key comes from the
trusted peer registry, and MeshReceiptSender rechecks that contact before sending.
The actor-signed consent occupies one immutable encrypted workflow slot per original.
No text, token, radio operation, fetch-side effect or automatic retry is added.

Integration: use intent timestamps/bindings with MeshReceiptSender.queue(kind='read')
and return permit(session, action) from its policy callback. Runtime must authorize
ReceiptAction separately and construct MeshUserContext with these exact bindings.
Never replace an uncertain sender dispatch with a new receipt or retry permission.
"""

from dataclasses import asdict, dataclass
from functools import wraps
import hashlib
import json
import re
import threading
import time
import uuid

from nacl.signing import SigningKey

from .mesh_envelope import Peer
from .mesh_inbox import MeshInbox
from .mesh_receipt_sender import ReceiptAction, ReceiptPermit, NAMESPACE as RECEIPT_NAMESPACE
from .message_contacts import RecipientID
from .workflow_store import WorkflowStore, WorkflowOutcomeUnknown


SCHEMA = "sage.lantern.mesh-read-consent.v1"
SIGN_DOMAIN = b"SAGE-LANTERN-RENDER-READ-CONSENT-v1\x00"
NAMESPACE = uuid.UUID("7ebc4444-ea0c-4f62-89ef-2398f929c2e8")
MAX_CHALLENGES = 32


class ReadReceiptError(RuntimeError):
    def __init__(self):
        super().__init__("read_receipt_unavailable")


def require(value):
    if value is not True:
        raise ReadReceiptError()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def identifier(value):
    require(type(value) is str and len(value) == 36 and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0)
    return value


def digest(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)
    return value


@dataclass(frozen=True, repr=False)
class ReadReceiptSession:
    actor: RecipientID
    epoch: str
    session_binding: str


@dataclass(frozen=True)
class RenderChallenge:
    challenge_id: str
    expires_at: int


@dataclass(frozen=True, repr=False)
class ReadReceiptIntent:
    local_id: str
    receipt_id: str
    radio_public_key: bytes
    issued_at: int
    expires_at: int
    privacy_binding: str
    render_ack_binding: str


def admitted(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._condition:
            require(not self._closed)
            self._active += 1
        try:
            return method(self, *args, **kwargs)
        except Exception:
            raise ReadReceiptError() from None
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()
    return call


class MeshReadReceipts:
    def __init__(self, store, identity, *, inbox, owner, authorize, clock=lambda: int(time.time())):
        try:
            require(type(store) is WorkflowStore and type(inbox) is MeshInbox and type(owner) is RecipientID
                    and inbox._store is store and store.storage.actor == owner
                    and inbox._recipient == Peer(owner.chain_id, bytes.fromhex(owner.agent_id)))
            require(isinstance(identity._signing_key, SigningKey) and identity.agent_id == owner.agent_id
                    and callable(authorize) and callable(clock))
        except Exception:
            raise ReadReceiptError() from None
        self.store, self.inbox, self.owner = store, inbox, owner
        self._identity, self._authorize, self._clock = identity, authorize, clock
        self._condition = threading.Condition()
        self._challenges, self._unknown = {}, set()
        self._active, self._closed, self._last = 0, False, None

    def _now(self):
        now = self._clock()
        with self._condition:
            require(type(now) is int and 1 <= now <= 2 ** 53 - 3601 and (self._last is None or now >= self._last))
            self._last = now
        return now

    def _check(self, session, operation):
        require(type(session) is ReadReceiptSession and session.actor == self.owner
                and type(session.epoch) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", session.epoch) is not None)
        digest(session.session_binding)
        with self._condition:
            require(not self._closed)
        require(self._authorize(session, operation) is True)
        with self._condition:
            require(not self._closed)
        require(self.store.storage.actor == self.owner and self.inbox._store is self.store)
        return True

    def _slot(self, local_id):
        identifier(local_id)
        return str(uuid.uuid5(NAMESPACE, encoded([asdict(self.owner), local_id]).decode()))

    def _original(self, local_id):
        identifier(local_id)
        original = self.inbox.historical_read(local_id)
        require(original.kind == "message" and original.recipient == Peer(self.owner.chain_id, bytes.fromhex(self.owner.agent_id)))
        return dict(local_id=local_id, original_sender=original.sender.to_dict(), original_id=original.message_id,
                    original_digest=original.digest)

    def _session(self, session):
        return dict(actor=asdict(session.actor), epoch=session.epoch, session_binding=session.session_binding)

    def _receipt_id(self, local_id):
        owner = Peer(self.owner.chain_id, bytes.fromhex(self.owner.agent_id))
        return str(uuid.uuid5(RECEIPT_NAMESPACE, encoded([owner.to_dict(), local_id, "read"]).decode()))

    def _intent(self, body):
        return ReadReceiptIntent(body["local_id"], self._receipt_id(body["local_id"]), bytes.fromhex(body["radio_public_key"]),
                                 body["issued_at"], body["expires_at"],
                                 hashlib.sha256(b"privacy\x00" + encoded(body)).hexdigest(),
                                 hashlib.sha256(b"render\x00" + encoded(body)).hexdigest())

    def _load(self, session, local_id, operation):
        self._check(session, operation)
        row = self.store.get(self._slot(local_id), kind="mesh_outbound", access_check=lambda: self._check(session, operation))
        require(row["revision"] == 1)
        proof = row["payload"]
        require(type(proof) is dict and set(proof) == {"body", "signature"}
                and type(proof["signature"]) is str and re.fullmatch(r"[0-9a-f]{128}", proof["signature"]) is not None)
        body = proof["body"]
        self._identity._signing_key.verify_key.verify(SIGN_DOMAIN + encoded(body), bytes.fromhex(proof["signature"]))
        require(type(body) is dict and set(body) == {"schema", "session", "local_id", "original_sender", "original_id", "original_digest",
            "radio_public_key", "challenge_id", "issued_at", "expires_at", "rendered", "share_read_receipt"})
        require(body["schema"] == SCHEMA and body["session"] == self._session(session)
                and body["rendered"] is True and body["share_read_receipt"] is True)
        identifier(body["challenge_id"])
        digest(body["radio_public_key"])
        require(type(body["issued_at"]) is int and type(body["expires_at"]) is int
                and 1 <= body["issued_at"] <= self._now() < body["expires_at"] <= body["issued_at"] + 300)
        original = self._original(local_id)
        require(all(body[key] == value for key, value in original.items()))
        self._check(session, operation)
        return body

    @admitted
    def begin_render(self, session, *, local_id, radio_public_key):
        self._check(session, "begin_render")
        require(type(radio_public_key) is bytes and len(radio_public_key) == 32)
        original = self._original(local_id)
        now = self._now()
        self._check(session, "begin_render")
        challenge = RenderChallenge(str(uuid.uuid4()), now + 60)
        with self._condition:
            require(not self._closed)
            self._challenges = {key: value for key, value in self._challenges.items() if value["expires_at"] > now}
            require(len(self._challenges) < MAX_CHALLENGES)
            self._challenges[challenge.challenge_id] = dict(original=original, session=self._session(session),
                                                           radio_public_key=radio_public_key.hex(), expires_at=challenge.expires_at)
        return challenge

    @admitted
    def acknowledge_render(self, session, *, challenge_id, rendered, share_read_receipt):
        self._check(session, "acknowledge_render")
        require(rendered is True and share_read_receipt is True)
        identifier(challenge_id)
        now = self._now()
        with self._condition:
            challenge = self._challenges.get(challenge_id)
            require(challenge is not None and challenge["session"] == self._session(session) and now < challenge["expires_at"])
            del self._challenges[challenge_id]
        original = challenge["original"]
        local_id = original["local_id"]
        require(self._original(local_id) == original)
        self._check(session, "acknowledge_render")
        now = self._now()
        require(now < challenge["expires_at"])
        body = dict(schema=SCHEMA, session=self._session(session), **original, radio_public_key=challenge["radio_public_key"],
                    challenge_id=challenge_id, issued_at=now, expires_at=now + 300, rendered=True, share_read_receipt=True)
        proof = dict(body=body, signature=self._identity._signing_key.sign(SIGN_DOMAIN + encoded(body)).signature.hex())
        def check():
            self._check(session, "acknowledge_render")
            require(self._now() < challenge["expires_at"])
            return True
        try:
            self.store.put(self._slot(local_id), "mesh_outbound", 0, proof, access_check=check)
        except WorkflowOutcomeUnknown:
            with self._condition:
                self._unknown.add(local_id)
            raise
        return self._intent(self._load(session, local_id, "acknowledge_render"))

    @admitted
    def recover(self, session, *, local_id):
        """Read exact signed approval after an uncertain journal ACK; never resend."""
        body = self._load(session, local_id, "recover")
        with self._condition:
            self._unknown.discard(local_id)
        return self._intent(body)

    @admitted
    def permit(self, session, action):
        require(type(action) is ReceiptAction and action.kind == "read" and action.operation in {"queue", "dispatch"}
                and action.decision_binding == "")
        body = self._load(session, action.local_id, "permit_" + action.operation)
        intent = self._intent(body)
        require(action.original_sender.to_dict() == body["original_sender"] and action.original_id == body["original_id"]
                and action.original_digest == body["original_digest"] and action.receipt_id == intent.receipt_id
                and action.radio_public_key == intent.radio_public_key)
        self._check(session, "permit_" + action.operation)
        return ReceiptPermit(action, intent.privacy_binding, intent.render_ack_binding)

    def close(self, timeout=0):
        require(type(timeout) in (int, float) and 0 <= timeout <= 30)
        with self._condition:
            self._closed = True
            self._challenges.clear()
            joined = self._condition.wait_for(lambda: self._active == 0, timeout)
            return joined and not self._unknown
