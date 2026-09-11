"""Root-bound render assertions; fetches never authorize receipts."""

from contextlib import contextmanager
from dataclasses import asdict
import re
import threading
import time
import uuid

from .mesh_read_receipts import (MeshReadReceipts, ReadReceiptSession, SCHEMA, SIGN_DOMAIN,
                                 NAMESPACE, encoded, identifier, digest)
from .mesh_receipt_sender import ReceiptAction
from .message_contacts import RecipientID


def require(value):
    if value is not True:
        raise ValueError("read_receipt_unavailable")


def verify_consent_row(row, *, identity, owner, inbox):
    require(row["kind"] == "mesh_outbound" and row["revision"] == 1 and row["agent_id"] == owner.agent_id)
    proof = row["payload"]
    require(type(proof) is dict and set(proof) == {"body", "signature"})
    require(type(proof["signature"]) is str and re.fullmatch(r"[0-9a-f]{128}", proof["signature"]) is not None)
    body = proof["body"]
    require(identity.agent_id == owner.agent_id)
    identity._signing_key.verify_key.verify(SIGN_DOMAIN + encoded(body), bytes.fromhex(proof["signature"]))
    require(type(body) is dict and set(body) == {"schema", "session", "local_id", "original_sender", "original_id",
        "original_digest", "radio_public_key", "challenge_id", "issued_at", "expires_at", "rendered", "share_read_receipt"})
    require(body["schema"] == SCHEMA and body["rendered"] is True and body["share_read_receipt"] is True)
    for key in ("local_id", "original_id", "challenge_id"):
        identifier(body[key])
    for key in ("original_digest", "radio_public_key"):
        digest(body[key])
    session = body["session"]
    require(type(session) is dict and set(session) == {"actor", "epoch", "session_binding"}
            and session["actor"] == asdict(owner))
    digest(session["session_binding"])
    require(type(session["epoch"]) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", session["epoch"]) is not None)
    require(type(body["issued_at"]) is int and type(body["expires_at"]) is int
            and 1 <= body["issued_at"] < body["expires_at"] <= min(2 ** 53 - 1, body["issued_at"] + 300))
    require(row["record_id"] == str(uuid.uuid5(NAMESPACE, encoded([asdict(owner), body["local_id"]]).decode())))
    original = inbox.historical_read(body["local_id"])
    require(original.kind == "message" and original.sender.to_dict() == body["original_sender"]
            and original.message_id == body["original_id"] and original.digest == body["original_digest"]
            and RecipientID(original.recipient.chain_id, original.recipient.public_key.hex()) == owner)
    return True


class RootReadReceipts:
    def __init__(self, provider, identity, *, active):
        self.provider, self.runtime, self.active = provider, provider._runtime, active
        self._mutex = threading.Lock()
        self._contexts, self._challenges = {}, {}
        self._pending = {}
        self._previous_authorize = self.runtime._authorize_context
        self._previous_policy = self.runtime._receipt_policy
        self.helper = MeshReadReceipts(self.runtime.store, identity, inbox=self.runtime.inbox,
            owner=self.runtime.actor, authorize=self._authorize_session)
        self.runtime._authorize_context = self._authorize_context
        self.runtime._receipt_policy = self._permit

    def _record(self, context):
        with self._mutex:
            return self._contexts.get(id(context))

    def _authorize_context(self, context, operation, action):
        record = self._record(context)
        if record is None:
            return self._previous_authorize(context, operation, action)
        return (record["context"] is context and self.active() is True and record["access"]() is True
                and operation == record["operation"] and action is None)

    def _authorize_session(self, session, operation):
        from .mesh_runtime import MeshReceiveContext
        context = getattr(self.runtime._local, "context", None)
        if type(context) is MeshReceiveContext:
            selected = getattr(self.runtime._local, "read_receipt_session", None)
            return (operation == "permit_dispatch" and selected is not None and selected[0] == session
                    and selected[1]() is True and self.active() is True and self.runtime._access() is True)
        record = self._record(context)
        allowed = {"render_challenge": {"begin_render"}, "rendered": {"acknowledge_render"},
                   "queue_receipt": {"permit_queue"}}
        return (record is not None and record["session"] == session and operation in allowed[record["operation"]]
                and self._authorize_context(context, record["operation"], None))

    def _permit(self, context, action):
        from .mesh_runtime import MeshReceiveContext
        if type(context) is MeshReceiveContext:
            if type(action) is not ReceiptAction or action.kind != "read" or action.operation != "dispatch":
                return self._previous_policy(context, action)
            with self._mutex:
                selected = self._pending.get(action.receipt_id)
            if selected is None or selected[1]() is not True or self.active() is not True:
                return None
            require(self.runtime._access() is True)
            self.runtime._local.read_receipt_session = selected
            try:
                return self.helper.permit(selected[0], action)
            finally:
                del self.runtime._local.read_receipt_session
        record = self._record(context)
        if record is None:
            return self._previous_policy(context, action)
        require(type(action) is ReceiptAction and action.kind == "read" and action.operation == "queue"
                and record["operation"] == "queue_receipt")
        return self.helper.permit(record["session"], action)

    @contextmanager
    def _grant(self, session, access, operation, intent=None):
        from .mesh_runtime import MeshUserContext
        context = MeshUserContext(session.actor, session.epoch,
            intent.privacy_binding if intent else session.session_binding, intent.render_ack_binding if intent else None)
        record = dict(context=context, session=session, access=access, operation=operation)
        with self._mutex:
            self._contexts[id(context)] = record
        try:
            require(self._authorize_context(context, operation, None))
            yield context
        finally:
            with self._mutex:
                self._contexts.pop(id(context), None)

    def invoke(self, method, arguments, access, binding, background_check=None):
        require(type(binding) is tuple and len(binding) == 2)
        epoch, session_binding = binding
        require(type(epoch) is str and re.fullmatch(r"[0-9a-f]{32}", epoch) is not None)
        digest(session_binding)
        session = ReadReceiptSession(self.runtime.actor, epoch, session_binding)
        require(arguments["rendered"] is True and access() is True and self.active() is True)
        if method == "render_challenge":
            item = self.provider.open_message(arguments["id"], access_check=access)
            require(item.box == "inbox" and item.id == arguments["id"])
            with self._grant(session, access, method) as context:
                def begin(device):
                    cards = [card for card in self.runtime.registry.list_current(access_check=self.runtime._access)
                             if card.recipient == item.sender]
                    require(len(cards) == 1)
                    radio = bytes.fromhex(cards[0].radio_public_key)
                    require(self.runtime.registry.resolve(radio, access_check=self.runtime._access) == cards[0])
                    return self.helper.begin_render(session, local_id=item.local_id, radio_public_key=radio)
                challenge = self.runtime._run(context, method, begin)
            self._challenges = {key: value for key, value in self._challenges.items() if value["expires_at"] > int(time.time())}
            require(len(self._challenges) < 32)
            self._challenges[challenge.challenge_id] = dict(id=item.id, session=session, expires_at=challenge.expires_at)
            result = dict(id=item.id, **asdict(challenge))
        else:
            require(method == "rendered" and arguments["share_read_receipt"] is True)
            saved = self._challenges.get(arguments["challenge_id"])
            require(saved is not None and saved["id"] == arguments["id"] and saved["session"] == session)
            with self._grant(session, access, method) as context:
                intent = self.runtime._run(context, method, lambda device: self.helper.acknowledge_render(session,
                    challenge_id=arguments["challenge_id"], rendered=arguments["rendered"], share_read_receipt=arguments["share_read_receipt"]))
            self._challenges.pop(arguments["challenge_id"], None)
            with self._grant(session, access, "queue_receipt", intent) as context:
                progress = self.runtime.queue_receipt(context, local_id=intent.local_id, kind="read",
                    radio_public_key=intent.radio_public_key, issued_at=intent.issued_at, expires_at=intent.expires_at)
            require(progress.receipt_id == intent.receipt_id and progress.state == "queued")
            if callable(background_check):
                require(background_check() is True)
                deadline = time.monotonic() + max(0, intent.expires_at - time.time())
                def live():
                    return time.monotonic() < deadline and background_check() is True
                with self._mutex:
                    self._pending = {key: value for key, value in self._pending.items() if value[1]() is True}
                    require(len(self._pending) < 32)
                    self._pending[intent.receipt_id] = (session, live)
            result = dict(id=arguments["id"], receipt_id=progress.receipt_id, state="queued")
        require(access() is True and self.active() is True)
        return result

    def close(self, timeout):
        self._challenges.clear()
        with self._mutex:
            self._pending.clear()
        return self.helper.close(timeout=min(30, max(0, timeout))) is True
