"""Durable custodian store: hold sealed deposits for absent peers.

The service owns the policy the codec deliberately leaves out: which contacts
may deposit, how much is stored, deduplication, expiry accounting and the
handover inventory. It stores one protected workflow record per deposit and
never opens an envelope, never decrypts, never re-seals and never claims
delivery.

Records use the existing ``mesh_inbound`` workflow kind with a distinct
payload schema rather than a new kind. That is deliberate: ``WorkflowStore``
validates every scanned record's kind against its local ``KINDS`` set, so a
new kind would make an older runtime refuse any page containing a custody
row. Every existing ``mesh_inbound`` consumer filters by payload schema, so an
unknown schema under that kind is ignored by older code (the same
old/new-tolerated pattern the outbox used for its progress body).

This module does no radio or transport work. Admission here means "stored",
never "delivered".
"""

import re
import time
import uuid

from .mesh_custody import CustodyDeposit, CustodyError, parse_custody
from .workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore


SCHEMA = "sage.lantern.mesh-custody.v1"
STATUS_SCHEMA = "sage.lantern.mesh-custody-status.v1"
KIND = "mesh_inbound"
STATE_HELD = "held"
STATE_DELIVERED = "delivered"
MAX_ENTRIES = 256
MAX_BYTES = 1 << 20
MAX_PER_RECIPIENT = 16
MAX_PER_SOURCE = 64
MAX_HANDOVER_ATTEMPTS = 8
MAX_HANDOVER_INDEX = 4096 // 48 + 1
_NAMESPACE = uuid.UUID("9f2c1f6a-5b64-4e0e-9c2f-3c9d0a4b8e21")
_FIELDS = {"schema", "state", "recipient_prefix", "message_digest", "expires_at",
           "stored_at", "resolved_at", "source", "operation_id", "wire",
           "handover_count", "handover_next", "handed_over_at"}


class CustodianError(RuntimeError):
    def __init__(self):
        super().__init__("custody_unavailable")


def valid_source(value):
    """A depositor identity is ``chain_id:radio-public-key`` exactly."""
    if type(value) is not str or not 3 <= len(value) <= 160:
        return False
    chain, _, key = value.rpartition(":")
    return (re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", chain) is not None
            and re.fullmatch(r"[0-9a-f]{64}", key) is not None)


def validate_status(value):
    """Strict wire schema for the counts-only status route. No content."""
    if (type(value) is not dict
            or set(value) != {"schema", "enabled", "held", "bytes", "recipients",
                              "oldest_expires_at", "delivered", "depositors"}
            or value["schema"] != STATUS_SCHEMA or type(value["enabled"]) is not bool
            or any(type(value[name]) is not int or not 0 <= value[name] <= 2 ** 53 - 1
                   for name in ("held", "bytes", "recipients", "delivered", "depositors"))
            or value["held"] > MAX_ENTRIES or value["depositors"] > 64
            or (value["oldest_expires_at"] is not None
                and (type(value["oldest_expires_at"]) is not int or value["oldest_expires_at"] <= 0))
            or (not value["enabled"] and any(value[name] for name in
                ("held", "bytes", "recipients", "delivered", "depositors")))):
        raise CustodianError()
    return value


def record_id(recipient_prefix, message_digest):
    """Stable record identity: one stored copy per recipient + message digest."""
    if type(recipient_prefix) is not bytes or len(recipient_prefix) != 6:
        raise CustodianError()
    if type(message_digest) is not bytes or len(message_digest) != 16:
        raise CustodianError()
    return str(uuid.uuid5(_NAMESPACE, f"{recipient_prefix.hex()}:{message_digest.hex()}"))


def _operation(value):
    if value is None:
        return None
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value or not parsed.int:
            raise ValueError
        return value
    except Exception:
        raise CustodianError() from None


class MeshCustodian:
    def __init__(self, store, *, access_check, depositors, clock=time.time,
                 max_entries=MAX_ENTRIES, max_bytes=MAX_BYTES,
                 max_per_recipient=MAX_PER_RECIPIENT, max_per_source=MAX_PER_SOURCE):
        try:
            if type(store) is not WorkflowStore or not callable(access_check) or not callable(clock):
                raise CustodianError()
            if (type(depositors) not in (tuple, list) or not 1 <= len(depositors) <= 64
                    or any(valid_source(item) is not True for item in depositors)):
                raise CustodianError()
            if (not 1 <= max_entries <= 4096 or not 64 <= max_bytes <= 1 << 24
                    or not 1 <= max_per_recipient <= max_entries
                    or not 1 <= max_per_source <= 4096):
                raise CustodianError()
        except Exception:
            raise CustodianError() from None
        self._store = store
        self._access = access_check
        self._clock = clock
        self._depositors = frozenset(depositors)
        self._bounds = (max_entries, max_bytes, max_per_recipient, max_per_source)

    def _now(self):
        clock = self._clock()
        if type(clock) not in (int, float) or not 0 <= clock <= 2 ** 53 - 1:
            raise CustodianError()
        return int(clock)

    @staticmethod
    def _verified(record):
        payload = record["payload"]
        if type(payload) is not dict or set(payload) != _FIELDS or payload["schema"] != SCHEMA:
            raise CustodianError()
        if (type(payload["recipient_prefix"]) is not str or re.fullmatch(r"[0-9a-f]{12}", payload["recipient_prefix"]) is None
                or type(payload["message_digest"]) is not str or re.fullmatch(r"[0-9a-f]{32}", payload["message_digest"]) is None
                or type(payload["expires_at"]) is not int or type(payload["stored_at"]) is not int
                or (payload["resolved_at"] is not None and type(payload["resolved_at"]) is not int)
                or type(payload["handover_count"]) is not int
                or not 0 <= payload["handover_count"] <= MAX_HANDOVER_ATTEMPTS
                or type(payload["handover_next"]) is not int
                or not 0 <= payload["handover_next"] <= MAX_HANDOVER_INDEX
                or (payload["handed_over_at"] is not None and type(payload["handed_over_at"]) is not int)
                or (payload["handover_count"] == 0) != (payload["handed_over_at"] is None)
                or payload["state"] not in (STATE_HELD, STATE_DELIVERED)
                or (payload["state"] == STATE_HELD) != (payload["resolved_at"] is None)
                or valid_source(payload["source"]) is not True
                or type(payload["wire"]) is not str):
            raise CustodianError()
        _operation(payload["operation_id"])
        try:
            deposit = parse_custody(payload["wire"])
        except CustodyError:
            raise CustodianError() from None
        if (deposit.recipient_prefix.hex() != payload["recipient_prefix"]
                or deposit.message_digest.hex() != payload["message_digest"]
                or deposit.expires_at != payload["expires_at"]):
            raise CustodianError()
        return deposit

    def _rows(self):
        try:
            records = self._store.scan(limit=50, max_records=4096, access_check=self._access)
        except WorkflowOutcomeUnknown:
            raise CustodianError() from None
        except Exception:
            raise CustodianError() from None
        rows = []
        for record in records:
            if record["kind"] == KIND and type(record["payload"]) is dict \
                    and record["payload"].get("schema") == SCHEMA:
                rows.append((record, self._verified(record)))
        return rows

    def admit(self, deposit, *, source, operation_id=None, now=None):
        """Store a deposit; returns stored/duplicate/expired/refused/rejected."""
        now = self._now() if now is None else now
        if (type(deposit) is not CustodyDeposit or type(now) is not int
                or valid_source(source) is not True):
            raise CustodianError()
        _operation(operation_id)
        if deposit.expires_at <= now:
            return "expired"
        if source not in self._depositors:
            return "refused"
        try:
            identifier = record_id(deposit.recipient_prefix, deposit.message_digest)
            rows = self._rows()
            existing = next((row for row in rows if row[0]["record_id"] == identifier), None)
            if existing is not None:
                prior, prior_deposit = existing
                if prior["payload"]["state"] == STATE_HELD and prior["payload"]["expires_at"] > now:
                    return "duplicate" if prior_deposit.to_wire() == deposit.to_wire() else "rejected"
                base = prior["revision"]
            else:
                base = 0
            held = [row for row in rows if row[0]["payload"]["state"] == STATE_HELD
                    and row[1].expires_at > now and row[0]["record_id"] != identifier]
            entries, max_bytes, max_recipient, max_source = self._bounds
            size = len(deposit.envelope_wire)
            if (len(held) >= entries
                    or sum(len(row[1].envelope_wire) for row in held) + size > max_bytes
                    or sum(row[1].recipient_prefix == deposit.recipient_prefix for row in held) >= max_recipient
                    or sum(row[0]["payload"]["source"] == source for row in held) >= max_source):
                return "rejected"
            payload = dict(schema=SCHEMA, state=STATE_HELD,
                           recipient_prefix=deposit.recipient_prefix.hex(),
                           message_digest=deposit.message_digest.hex(),
                           expires_at=deposit.expires_at, stored_at=now, resolved_at=None,
                           source=source, operation_id=operation_id, wire=deposit.to_wire(),
                           handover_count=0, handover_next=0, handed_over_at=None)
            self._store.put(identifier, KIND, base, payload, access_check=self._access)
            return "stored"
        except WorkflowOutcomeUnknown:
            raise CustodianError() from None
        except CustodianError:
            raise
        except Exception:
            raise CustodianError() from None

    def pending_for(self, recipient_prefix, *, now=None):
        now = self._now() if now is None else now
        if type(recipient_prefix) is not bytes or len(recipient_prefix) != 6 or type(now) is not int:
            raise CustodianError()
        rows = [row for row in self._rows()
                if row[1].recipient_prefix == recipient_prefix
                and row[0]["payload"]["state"] == STATE_HELD and row[1].expires_at > now]
        rows.sort(key=lambda row: (row[0]["payload"]["stored_at"], row[0]["record_id"]))
        return tuple(row[1] for row in rows)

    def handover_for(self, recipient_prefix, *, now=None, min_interval=0):
        """Held copies to hand over now: (deposit, next fragment, passes done).

        A completed pass is re-sent only after ``min_interval`` seconds and at
        most MAX_HANDOVER_ATTEMPTS times; a copy that is never delivered stays
        held until its TTL, because the custodian cannot see the recipient's
        receipt (that goes to the original sender).
        """
        now = self._now() if now is None else now
        if type(recipient_prefix) is not bytes or len(recipient_prefix) != 6 \
                or type(now) is not int or type(min_interval) not in (int, float) or min_interval < 0:
            raise CustodianError()
        result = []
        for record, deposit in self._rows():
            payload = record["payload"]
            if (deposit.recipient_prefix != recipient_prefix or payload["state"] != STATE_HELD
                    or deposit.expires_at <= now or payload["handover_count"] >= MAX_HANDOVER_ATTEMPTS):
                continue
            if payload["handed_over_at"] is not None and now - payload["handed_over_at"] < min_interval:
                continue
            result.append((record, deposit))
        result.sort(key=lambda row: (row[0]["payload"]["stored_at"], row[0]["record_id"]))
        return tuple((deposit, record["payload"]["handover_next"], record["payload"]["handover_count"])
                     for record, deposit in result)

    def note_handover(self, recipient_prefix, message_digest, *, next_index, complete, now=None):
        """Record handover progress. Sending is not delivery: this only bounds
        retries and lets the next wake continue a long envelope."""
        now = self._now() if now is None else now
        if (type(next_index) is not int or not 0 <= next_index <= MAX_HANDOVER_INDEX
                or type(complete) is not bool or type(now) is not int):
            raise CustodianError()
        identifier = record_id(recipient_prefix, message_digest)
        rows = [row for row in self._rows() if row[0]["record_id"] == identifier]
        if not rows or rows[0][0]["payload"]["state"] != STATE_HELD:
            return False
        record, deposit = rows[0]
        payload = record["payload"]
        if deposit.expires_at <= now or payload["handover_count"] >= MAX_HANDOVER_ATTEMPTS:
            return False
        if complete:
            payload = dict(payload, handover_next=0, handover_count=payload["handover_count"] + 1,
                           handed_over_at=now)
        else:
            payload = dict(payload, handover_next=next_index)
        self._store.put(identifier, KIND, record["revision"], payload, access_check=self._access)
        return True

    def confirm_delivered(self, recipient_prefix, message_digest, *, now=None):
        now = self._now() if now is None else now
        identifier = record_id(recipient_prefix, message_digest)
        rows = [row for row in self._rows() if row[0]["record_id"] == identifier]
        if not rows or rows[0][0]["payload"]["state"] != STATE_HELD:
            return False
        record, _ = rows[0]
        payload = dict(record["payload"], state=STATE_DELIVERED, resolved_at=now)
        self._store.put(identifier, KIND, record["revision"], payload, access_check=self._access)
        return True

    def stats(self, *, now=None):
        now = self._now() if now is None else now
        rows = self._rows()
        held = [row for row in rows if row[0]["payload"]["state"] == STATE_HELD and row[1].expires_at > now]
        return dict(schema=STATUS_SCHEMA, enabled=True, held=len(held),
                    bytes=sum(len(row[1].envelope_wire) for row in held),
                    recipients=len({row[1].recipient_prefix for row in held}),
                    oldest_expires_at=min((row[1].expires_at for row in held), default=None),
                    delivered=sum(row[0]["payload"]["state"] == STATE_DELIVERED for row in rows),
                    depositors=len(self._depositors))
