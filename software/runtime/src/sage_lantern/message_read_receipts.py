"""Receipt-v2 SDK adapter; no wire crypto, network setup or automatic read ACK.

Reference: sibling SAGE sdk/python/src/sage_sdk/client.py receipt helpers and
api/rest/pipe_handler.go handlePipeReceiptStatus. The injected SDK client must
use the exact configured actor on its authenticated home node. Binding comes
from trusted message admission, not a browser or unverified radio envelope.
Content digest is SAGE's bound receipt digest, not an invented text hash.

The live guard must validate this exact open/session/binding and privacy consent
on every call. It must return literal True only after explicit authorized render.
Guards fence admission, not already dispatched remote mutations; cancellation
after dispatch returns unknown. No claim, receive, generic mark-read or fallback
API is called. Queued is NOT Read. Sender confirmation trusts the authenticated
SAGE projection; it is not independent cryptographic proof or a human-read claim
for a nested gateway message. Memory-only replay limits require fresh open
approvals after restart. This is an unintegrated release-candidate foundation.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Protocol

from .message_contacts import RecipientID


class ReceiptClient(Protocol):
    def pipe_receipt_challenge(self, pipe_id: str, kind: str) -> dict: ...
    def pipe_receipt_record(self, pipe_id: str, kind: str, challenge: dict) -> dict: ...
    def pipe_receipt_status(self, pipe_id: str) -> dict: ...


def _bounded(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[\x21-\x7e]{1,256}", value) is not None


@dataclass(frozen=True, slots=True)
class ReceiptBinding:
    sender: RecipientID
    recipient: RecipientID
    sender_pipe_id: str
    recipient_pipe_id: str
    message_id: str
    content_digest: str
    policy_epoch: str
    agreement_id: str
    contact_id: str
    contact_revision: str
    authorization_mode: str = ""
    relation_digest: str = ""

    def __post_init__(self):
        if (type(self.sender) is not RecipientID or type(self.recipient) is not RecipientID
                or any(not _bounded(value) for value in (
                    self.sender_pipe_id, self.recipient_pipe_id, self.message_id,
                    self.policy_epoch, self.agreement_id, self.contact_id, self.contact_revision))
                or type(self.content_digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.content_digest) is None
                or type(self.authorization_mode) is not str or type(self.relation_digest) is not str
                or (self.authorization_mode != "" and not _bounded(self.authorization_mode))
                or (self.relation_digest != "" and re.fullmatch(r"[0-9a-f]{64}", self.relation_digest) is None)):
            raise ValueError("receipt_unavailable")

    def challenge_body(self) -> dict:
        body = {
            "version": 2, "message_id": self.message_id,
            "sender_chain_id": self.sender.chain_id, "recipient_chain_id": self.recipient.chain_id,
            "sender_agent_id": self.sender.agent_id, "recipient_agent_id": self.recipient.agent_id,
            "content_digest": self.content_digest, "event_kind": "read",
            "policy_epoch": self.policy_epoch, "agreement_id": self.agreement_id,
            "contact_id": self.contact_id, "contact_revision": self.contact_revision,
        }
        if self.authorization_mode:
            body["authorization_mode"] = self.authorization_mode
        if self.relation_digest:
            body["relation_digest"] = self.relation_digest
        return body


class QueueOutcome(StrEnum):
    NOT_REQUESTED = "not_requested"
    UNSUPPORTED = "unsupported"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    QUEUED = "queued"


class ReadProjection(StrEnum):
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    CONFIRMED = "confirmed"


Guard = Callable[[ReceiptBinding, str], bool]


class ReadReceiptAdapter:
    def __init__(self, *, client: ReceiptClient, actor: RecipientID,
                 guard: Guard | None = None, max_opens: int = 128):
        if type(actor) is not RecipientID or type(max_opens) is not int or not 1 <= max_opens <= 1024:
            raise ValueError("receipt_unavailable")
        self._client = client
        self._actor = actor
        self._guard = guard
        self._max_opens = max_opens
        self._used: set[str] = set()
        self._mutex = threading.Lock()

    def _allowed(self, binding: ReceiptBinding, session: str) -> bool:
        try:
            return self._guard is not None and self._guard(binding, session) is True
        except Exception:
            return False

    def acknowledge_open(self, binding: ReceiptBinding, *, open_id: str,
                         session_id: str, explicit_open: bool, privacy_enabled: bool,
                         receipt_protocol_version: int) -> QueueOutcome:
        if explicit_open is not True or privacy_enabled is not True:
            return QueueOutcome.NOT_REQUESTED
        if type(receipt_protocol_version) is not int or receipt_protocol_version != 2:
            return QueueOutcome.UNSUPPORTED
        if (type(binding) is not ReceiptBinding or self._actor != binding.recipient
                or not _bounded(open_id) or not _bounded(session_id)):
            return QueueOutcome.UNAVAILABLE
        if not self._allowed(binding, session_id):
            return QueueOutcome.CANCELLED
        with self._mutex:
            if open_id in self._used or len(self._used) >= self._max_opens:
                return QueueOutcome.UNAVAILABLE
            self._used.add(open_id)
        dispatched = False
        try:
            if not self._allowed(binding, session_id):
                return QueueOutcome.CANCELLED
            challenge = self._client.pipe_receipt_challenge(binding.recipient_pipe_id, "read")
            expected = {"pipe_id": binding.recipient_pipe_id, "event_kind": "read",
                        "challenge": binding.challenge_body()}
            if (type(challenge) is not dict or challenge != expected
                    or type(challenge.get("challenge")) is not dict
                    or type(challenge["challenge"].get("version")) is not int):
                return QueueOutcome.UNAVAILABLE
            if not self._allowed(binding, session_id):
                return QueueOutcome.CANCELLED
            dispatched = True
            result = self._client.pipe_receipt_record(binding.recipient_pipe_id, "read", challenge)
            if not self._allowed(binding, session_id):
                return QueueOutcome.UNKNOWN
            if (type(result) is not dict
                    or set(result) != {"pipe_id", "event_kind", "receipt_status", "idempotent_replay"}
                    or result["pipe_id"] != binding.recipient_pipe_id or result["event_kind"] != "read"
                    or result["receipt_status"] != "queued" or type(result["idempotent_replay"]) is not bool):
                return QueueOutcome.UNKNOWN
            return QueueOutcome.QUEUED
        except Exception:
            return QueueOutcome.UNKNOWN if dispatched else QueueOutcome.UNAVAILABLE

    def sender_status(self, binding: ReceiptBinding, *, session_id: str) -> ReadProjection:
        if (type(binding) is not ReceiptBinding or self._actor != binding.sender
                or not _bounded(session_id) or not self._allowed(binding, session_id)):
            return ReadProjection.UNKNOWN
        try:
            result = self._client.pipe_receipt_status(binding.sender_pipe_id)
            if not self._allowed(binding, session_id) or type(result) is not dict:
                return ReadProjection.UNKNOWN
            if result.get("pipe_id") != binding.sender_pipe_id:
                return ReadProjection.UNKNOWN
            if result.get("protocol") == "unsupported":
                return ReadProjection.UNSUPPORTED
            if (result.get("protocol") == "receipt-v2" and result.get("read_status") == "confirmed"
                    and type(result.get("read_at")) is str and 1 <= len(result["read_at"]) <= 64
                    and result.get("claim_status") in ("confirmed", "unconfirmed")
                    and _bounded(result.get("transport_status"))):
                return ReadProjection.CONFIRMED
            return ReadProjection.UNKNOWN
        except Exception:
            return ReadProjection.UNKNOWN
