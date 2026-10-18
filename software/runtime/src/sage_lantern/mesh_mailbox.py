"""Separate read-only mesh provider, not a SAGE SDK-shaped transport substitute.

list_messages(box, limit=20, access_check=...) -> MailboxPage;
open_message(id, access_check=...) -> MailboxItem. Neither marks read, generates
receipts, dequeues, transmits, adopts contacts nor changes workflow rows.

producer_fence() MUST acquire/join all relevant producers across processes and
yield a callable lease check, holding that fence through the operation. A dummy
True callback is not an implemented fence. WorkflowStore.scan is sequential,
not a snapshot. The factory owns fencing and bounded context-manager execution.
Schemas are only candidate filters; every displayed message is verified through
MeshInbox.historical_read or MeshOutbox.read_sent. No cached text is trusted.

state is UI-compatible; workflow_state preserves exact local progress. 'sent'
means awaiting a genuine receipt, never delivered. Inbox 'unknown' makes no read
or receipt claim. Full qualified peers remain typed; opaque IDs bind those peers
and both message identities, and support direct lookup beyond a listing page.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from .mesh_envelope import OpenedEnvelope
from .mesh_inbox import INBOX_SCHEMA, MeshInbox, inbox_id
from .mesh_outbox import SCHEMAS as OUTBOX_SCHEMAS, MeshOutbox, SentMessage
from .message_contacts import RecipientID
from .workflow_store import MAX_RECORDS, WorkflowStore


MAX_ITEMS = 20
_STATES = {'queued': 'queued', 'dispatching': 'unknown', 'awaiting_receipt': 'sent',
           'delivered': 'delivered', 'read': 'read', 'cancelled': 'cancelled'}


class MailboxError(RuntimeError):
    def __init__(self):
        super().__init__('mesh_mailbox_unavailable')


@dataclass(frozen=True, repr=False)
class MailboxItem:
    id: str
    box: str
    local_id: str
    message_id: str
    sender: RecipientID
    recipient: RecipientID
    text: str
    created_at: str
    state: str
    workflow_state: str


@dataclass(frozen=True, repr=False)
class MailboxPage:
    items: tuple[MailboxItem, ...]
    more: bool


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
        raise MailboxError()


def _peer(value):
    return RecipientID(value.chain_id, value.public_key.hex())


def _timestamp(value):
    if type(value) is not int or not 0 <= value <= (1 << 53) - 1:
        raise MailboxError()
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


class MeshMailbox:
    def __init__(self, store, inbox, outbox, *, owner, producer_fence, max_records=MAX_RECORDS):
        try:
            if (type(store) is not WorkflowStore or type(inbox) is not MeshInbox or type(outbox) is not MeshOutbox
                    or type(owner) is not RecipientID or store.storage.actor != owner
                    or inbox._store is not store or outbox.store is not store
                    or _peer(inbox._recipient) != owner or _peer(outbox.sender) != owner
                    or not callable(producer_fence) or type(max_records) is not int
                    or not 1 <= max_records <= MAX_RECORDS):
                raise MailboxError()
        except Exception:
            raise MailboxError() from None
        self.store, self.inbox, self.outbox, self.owner = store, inbox, outbox, owner
        self.producer_fence, self.max_records = producer_fence, max_records

    def _access(self, access_check):
        if (not callable(access_check) or access_check() is not True or self.store.storage.actor != self.owner
                or self.inbox._store is not self.store or self.outbox.store is not self.store
                or _peer(self.inbox._recipient) != self.owner or _peer(self.outbox.sender) != self.owner):
            raise MailboxError()
        return True

    @contextmanager
    def _read(self, access_check):
        try:
            self._access(access_check)
            complete = False
            with self.producer_fence() as lease:
                if not callable(lease):
                    raise MailboxError()
                def check():
                    if self._access(access_check) is not True or lease() is not True:
                        raise MailboxError()
                    return True
                check()
                epoch = self.store.storage.snapshot().epoch
                check()
                yield check
                check()
                if self.store.storage.snapshot().epoch != epoch:
                    raise MailboxError()
                check()
                complete = True
            if not complete:
                raise MailboxError()
            self._access(access_check)
        except Exception:
            raise MailboxError() from None

    def _id(self, box, local_id, message_id, sender, recipient):
        binding = dict(schema='sage.lantern.mesh-mailbox-id.v1', box=box, local_id=local_id,
                       message_id=message_id, owner=[self.owner.chain_id, self.owner.agent_id],
                       sender=[sender.chain_id, sender.agent_id], recipient=[recipient.chain_id, recipient.agent_id])
        digest = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return f'mesh:{box}:{local_id}:{digest}'

    @staticmethod
    def _candidate(record, box):
        payload = record['payload']
        if box == 'inbox':
            return record['kind'] == 'mesh_inbound' and type(payload) is dict and payload.get('schema') == INBOX_SCHEMA
        return (record['kind'] == 'mesh_outbound' and type(payload) is dict
                and type(payload.get('body')) is dict and payload['body'].get('schema') in OUTBOX_SCHEMAS)

    def _item(self, record, box, check):
        check()
        local_id = record['record_id']
        _uuid(local_id)
        if not self._candidate(record, box):
            return None
        if box == 'inbox':
            opened = self.inbox.historical_read(local_id)
            check()
            if type(opened) is not OpenedEnvelope or _peer(opened.recipient) != self.owner:
                raise MailboxError()
            if inbox_id(opened.sender, opened.message_id) != local_id or record['revision'] != 1:
                raise MailboxError()
            if opened.kind != 'message':
                return None
            sender, recipient = _peer(opened.sender), _peer(opened.recipient)
            message_id, text = opened.message_id, opened.content
            issued_at = opened.logical_issued_at if opened.logical_issued_at is not None else opened.issued_at
            state, workflow_state = 'unknown', 'received'
        else:
            sent = self.outbox.read_sent(local_id, access_check=check)
            check()
            if (type(sent) is not SentMessage or sent.progress.message_id != local_id
                    or sent.progress.revision != record['revision'] or sent.progress.state not in _STATES):
                raise MailboxError()
            sender, recipient = self.owner, _peer(sent.recipient)
            message_id, text, issued_at = local_id, sent.content, sent.issued_at
            workflow_state = sent.progress.state
            state = _STATES[workflow_state]
        _uuid(message_id)
        if type(text) is not str or not 1 <= len(text.encode('utf-8')) <= 512:
            raise MailboxError()
        return MailboxItem(self._id(box, local_id, message_id, sender, recipient), box, local_id, message_id,
                           sender, recipient, text, _timestamp(issued_at), state, workflow_state)

    def list_messages(self, box, *, limit=MAX_ITEMS, access_check):
        """Bounded full scan, newest signed creation time first; no partial scan fallback."""
        try:
            if type(box) is not str or box not in ('inbox', 'outbox') or type(limit) is not int or not 1 <= limit <= MAX_ITEMS:
                raise MailboxError()
            with self._read(access_check) as check:
                rows = self.store.scan(limit=50, max_records=self.max_records, access_check=check)
                items = []
                for row in rows:
                    item = self._item(row, box, check)
                    if item is not None:
                        items.append(item)
                items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
                result = MailboxPage(tuple(items[:limit]), len(items) > limit)
            return result
        except Exception:
            raise MailboxError() from None

    def open_message(self, identifier, *, access_check):
        """Direct attested read; does NOT mark read or authorize any read receipt."""
        try:
            if type(identifier) is not str:
                raise MailboxError()
            match = re.fullmatch(r'mesh:(inbox|outbox):([0-9a-f-]{36}):([0-9a-f]{64})', identifier)
            if match is None:
                raise MailboxError()
            box, local_id, _ = match.groups()
            _uuid(local_id)
            with self._read(access_check) as check:
                row = self.store.get(local_id, kind='mesh_inbound' if box == 'inbox' else 'mesh_outbound', access_check=check)
                result = self._item(row, box, check)
                if result is None or result.id != identifier:
                    raise MailboxError()
            return result
        except Exception:
            raise MailboxError() from None
