"""Canonical SAGE sends with durable content-free uncertainty markers.

The already-provisioned private directory holds only original request UUIDs.
SAGE owns encrypted payloads and atomic argument-bound idempotency. No local
keys, payload copies, fingerprint databases or automatic retries are introduced.
Existing markers deny new sends and clean drain until separately reconciled.
Lock does not authorize deleting a marker. The caller must bind its ordinary
agent, endpoint and recipient-consent verifier; this module activates nothing.
Atomic marker admission serializes independent senders, but empty-marker drain
evidence still requires every producer to be fenced and its admitted work joined.
"""

import copy
import os
import threading
import uuid

from .community_messaging import CommunityMessaging, MessagingUnavailable, SendOutcome
from .inference_guard import begin, pending_count
from .message_storage import MessageStorageProbe


class CanonicalMessageSender:
    def __init__(self, adapter, storage, directory):
        if (type(adapter) is not CommunityMessaging or type(storage) is not MessageStorageProbe
                or adapter._client is not storage.client or adapter._actor != storage.actor
                or not callable(adapter._prerequisites)):
            raise MessagingUnavailable()
        self.adapter = adapter
        self.storage = storage
        self.directory = directory
        self._mutex = threading.Lock()
        self._failed = False

    def bind(self, adapter):
        bound = copy.copy(self)
        bound.adapter = adapter
        base = adapter._prerequisites
        adapter._prerequisites = lambda: self.storage.prerequisites(base())
        return bound

    def drained(self):
        try:
            return not self._failed and pending_count(self.directory, owner_uid=os.geteuid()) == 0
        except Exception:
            return False

    def ready(self):
        if not self.drained():
            return False
        try:
            self.storage.snapshot()
            return True
        except Exception:
            return False

    def send(self, session_id, *, request_id, recipient, payload, access_check):
        marker = None
        dispatched_outcome = False
        if not self._mutex.acquire(blocking=False):
            raise MessagingUnavailable()
        try:
            if str(uuid.UUID(request_id)) != request_id or access_check() is not True or not self.ready():
                raise MessagingUnavailable()
            before = self.storage.snapshot()
            marker = begin(self.directory, request_id=uuid.UUID(request_id).hex, require_empty=True)
            if access_check() is not True:
                if marker.complete() is not True:
                    raise MessagingUnavailable()
                marker = None
                raise MessagingUnavailable()
            outcome = self.adapter.send(session_id, recipient=recipient, payload=payload,
                                        idempotency_key=request_id, ttl_minutes=1440, approved=True)
            dispatched_outcome = True
            if (type(outcome) is not SendOutcome or outcome.outcome != 'accepted_by_node'
                    or access_check() is not True or self.storage.snapshot() != before):
                raise MessagingUnavailable()
            if marker.complete() is not True:
                raise MessagingUnavailable()
            marker = None
            return outcome
        except Exception:
            if marker is not None:
                self._failed = True
            if marker is not None or dispatched_outcome:
                return SendOutcome('unknown')
            raise MessagingUnavailable() from None
        finally:
            self._mutex.release()
