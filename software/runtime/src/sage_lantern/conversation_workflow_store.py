"""ConversationStore interface over guarded, encrypted SAGE auxiliary records.

One deterministic actor control row fences consent, dispatch and history clear.
Each session stores text once and both observation operations in one CAS row.
No plaintext persistence, ledger deletion claim, background work or retry loop.
4096 is a shared server-row ceiling, not a promise of 10000 conversations.

Constructor access_check must be live and generation-bound, including at initial
control creation. approved seeds only a genuinely missing empty control. The
optional drain_check is a separate producer-fenced storage lease, revoked before
vault lock. No scan is an atomic snapshot of concurrently active producers.

reserve_media(session_id, digest, *, ticket, item, access_check) persists the
immutable item and pending intent together before caller blob PUT. commit_media
accepts only that MediaIntent under original consent and returns literal True.
list exposes photo_reference only after commit. Caller must verify PUT digest; this
store does not fetch, authenticate or delete blob bytes. Pending intents need
external reconciliation/cleanup, never a guessed retry or orphan deletion.

Large arbitrary suppress sets must fit one 16 KiB control CAS or fail unchanged;
clear advances one epoch instead of issuing a partially successful batch. All
session writes guard that exact control revision. No CAS failure is retried.
"""

from contextlib import contextmanager
import copy
from functools import wraps
import hashlib
import threading
from types import SimpleNamespace
from uuid import uuid4

from . import conversation_records as records
from .conversation_records import ConversationDrainSnapshot
from .conversation_store import ConversationStoreError, JournalEntry
from .workflow_store import KINDS, WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore


def boundary(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        try:
            self._check()
            return method(self, *args, **kwargs)
        except WorkflowOutcomeUnknown:
            raise ConversationStoreError("store_outcome_unknown") from None
        except ConversationStoreError:
            raise
        except Exception:
            raise ConversationStoreError("store_unavailable") from None
    return invoke


class ConversationWorkflowStore:
    def __init__(self, workflow, *, access_check, drain_check=None, approved=False):
        records.require(isinstance(workflow, WorkflowStore) and callable(access_check) and type(approved) is bool)
        records.require(drain_check is None or callable(drain_check))
        self._workflow = workflow
        self._access = access_check
        self._drain = drain_check
        self._closed = False
        self._binding = threading.local()
        self._owner = str(uuid4())
        actor = workflow.storage.actor
        self._actor = dict(chain_id=actor.chain_id, agent_id=actor.agent_id)
        self.control_id = records.record_id(self._actor)
        self._initialize(approved)

    def _check(self):
        records.require(not self._closed, "store_closed")
        records.require(getattr(self._binding, "access", self._access)() is True, "store_unavailable")
        actor = self._workflow.storage.actor
        records.require(self._actor == dict(chain_id=actor.chain_id, agent_id=actor.agent_id), "actor_changed")

    @boundary
    def _initialize(self, approved):
        try:
            self._control()
            return
        except WorkflowNotFound:
            rows = self._workflow.scan(limit=50, max_records=4096, access_check=self._check_access)
            records.require(not any(row["kind"] in {"conversation_control", "conversation_session"} for row in rows), "missing_control")
            records.require(len(rows) < 4096, "capacity_exceeded")
            value = dict(schema=records.CONTROL_SCHEMA, binding=self._actor, enabled=approved,
                         generation=str(uuid4()), history_generation=None, dispatch_epoch=str(uuid4()),
                         clear_epoch=str(uuid4()), suppressed=[])
            self._write_control(0, value)

    def _check_access(self):
        self._check()
        return True

    def _control(self):
        row = self._workflow.get(self.control_id, kind="conversation_control", access_check=self._check_access)
        records.require(row["record_id"] == self.control_id and row["kind"] == "conversation_control")
        records.validate_control(row["payload"], self._actor)
        return row

    def _write_control(self, revision, value):
        records.validate_control(value, self._actor)
        return self._workflow.put(self.control_id, "conversation_control", revision, value, access_check=self._check_access)

    def _session(self, session_id):
        row = self._workflow.get(records.record_id(self._actor, session_id), kind="conversation_session", access_check=self._check_access)
        self._validate_row(row)
        records.require(row["payload"]["session_id"] == session_id)
        return row

    def _validate_row(self, row):
        records.require(row["kind"] == "conversation_session")
        value = records.validate_session(row["payload"], self._actor)
        records.require(row["record_id"] == records.record_id(self._actor, value["session_id"]))

    def _write(self, row, control, value):
        records.validate_session(value, self._actor)
        return self._workflow.put(records.record_id(self._actor, value["session_id"]), "conversation_session",
            0 if row is None else row["revision"], value, access_check=self._check_access,
            guard=dict(record_id=self.control_id, expected_revision=control["revision"]))

    def _inventory(self):
        control = self._control()
        rows = self._workflow.scan(limit=50, max_records=4096, access_check=self._check_access)
        sessions = []
        for row in rows:
            if row["kind"] == "conversation_session":
                self._validate_row(row)
                sessions.append(row)
        records.require(self._control()["revision"] == control["revision"], "control_changed")
        return control, sessions, len(rows)

    @staticmethod
    def _ticket(control):
        value = control["payload"]
        if not value["enabled"]:
            return None
        if value["history_generation"] is None:
            return value["generation"]
        return dict(store_generation=value["generation"], history_generation=value["history_generation"])

    @staticmethod
    def _suppressed(value, control):
        return value["session_id"] in control["payload"]["suppressed"] or value["clear_epoch"] != control["payload"]["clear_epoch"]

    def _admission(self, value, control, *, dispatch=False):
        records.require(control["payload"]["enabled"] and not self._suppressed(value, control)
                        and value["dispatch_epoch"] == control["payload"]["dispatch_epoch"], "dispatch_denied")
        if dispatch:
            records.require(value["media"] is None or value["media"]["state"] == "linked", "media_pending")

    def _new_session(self, session_id, control):
        value = control["payload"]
        return dict(schema=records.SESSION_SCHEMA, binding=dict(self._actor), session_id=records.identifier(session_id),
                    item=None, item_hash=None, consent_generation=value["generation"], history_generation=value["history_generation"],
                    dispatch_epoch=value["dispatch_epoch"], clear_epoch=value["clear_epoch"], operations={}, media=None)

    def _capture(self, ticket, session_id):
        control = self._control()
        if ticket is None or ticket != self._ticket(control) or session_id in control["payload"]["suppressed"]:
            return control, None, None
        try:
            row = self._session(session_id)
        except WorkflowNotFound:
            checked, _, count = self._inventory()
            records.require(checked["revision"] == control["revision"], "control_changed")
            records.require(count < 4096, "capacity_exceeded")
            return control, None, self._new_session(session_id, control)
        value = row["payload"]
        if self._suppressed(value, control):
            return control, row, None
        self._admission(value, control)
        return control, row, value

    @boundary
    def approve(self, enabled, *, history_generation=None):
        records.require(type(enabled) is bool, "invalid_consent")
        records.history_generation(history_generation)
        control = self._control()
        value = copy.deepcopy(control["payload"])
        if value["enabled"] != enabled or value["history_generation"] != history_generation:
            if value["enabled"] and not enabled:
                value["dispatch_epoch"] = str(uuid4())
            value.update(enabled=enabled, history_generation=history_generation, generation=str(uuid4()))
            control = self._write_control(control["revision"], value)
        return self._ticket(control)

    @boundary
    def consent(self):
        return self._ticket(self._control())

    @boundary
    def enqueue(self, ticket, item):
        item = records.validate_item(item)
        control, row, value = self._capture(ticket, item["session_id"])
        if value is None:
            return []
        if value["item"] is not None:
            records.require(value["item"] == item, "payload_conflict")
        else:
            records.require(value["consent_generation"] == control["payload"]["generation"], "stale_capture")
            records.require(value["media"] is None or item["has_photo"], "media_conflict")
            value.update(item=item, item_hash=records.checksum(item), operations={kind: records.new_operation()
                for kind in ("interaction", "reflection") if kind == "interaction" or "reflection" in item})
            records.capacity(value, reserve_receipts=True)
            self._write(row, control, value)
        return [records.operation_id(item["session_id"], kind) for kind in sorted(value["operations"])]

    def _view(self, value, kind, control):
        operation = value["operations"][kind]
        state = operation["state"]
        suppressed = self._suppressed(value, control)
        revoked = value["dispatch_epoch"] != control["payload"]["dispatch_epoch"]
        if state == "queued" and (suppressed or revoked):
            state = "cancelled"
        elif state == "dispatching" and (suppressed or revoked or operation["owner"] != self._owner):
            state = "unknown"
        payload = records.payload(value["item"], kind)
        return dict(operation_id=records.operation_id(value["session_id"], kind), session_id=value["session_id"], kind=kind,
            payload=payload, payload_sha256=hashlib.sha256(payload.encode()).hexdigest(), consent_generation=value["consent_generation"],
            history_generation=value["history_generation"], state=state, memory_id=operation["memory_id"], sage_status=operation["sage_status"],
            deprecation_status=operation["deprecation_status"], consent_revoked=revoked, suppressed=suppressed)

    def _find(self, operation_id):
        records.identifier(operation_id)
        control, rows, _ = self._inventory()
        for row in rows:
            for kind in row["payload"]["operations"]:
                if records.operation_id(row["payload"]["session_id"], kind) == operation_id:
                    return control, row, kind
        raise ConversationStoreError("operation_not_found")

    @boundary
    def operation(self, operation_id):
        control, row, kind = self._find(operation_id)
        return self._view(row["payload"], kind, control)

    @boundary
    def work(self, session_id, kind):
        return self.operation(records.operation_id(session_id, kind))

    @boundary
    def operations(self, state=None, include_suppressed=False):
        records.require(state is None or state in records.STATES)
        records.require(type(include_suppressed) is bool)
        control, rows, _ = self._inventory()
        result = [self._view(row["payload"], kind, control) for row in rows for kind in sorted(row["payload"]["operations"])]
        return [value for value in result if (state is None or value["state"] == state) and (include_suppressed or not value["suppressed"])]

    @boundary
    def pending(self, limit=100):
        records.require(type(limit) is int and 1 <= limit <= 1000)
        if self.consent() is None:
            return []
        control, rows, _ = self._inventory()
        result = []
        for row in rows:
            value = row["payload"]
            if value["media"] is not None and value["media"]["state"] != "linked":
                continue
            for kind in sorted(value["operations"]):
                operation = self._view(value, kind, control)
                if operation["state"] == "queued" and not operation["suppressed"]:
                    result.append(operation)
        return result[:limit]

    @boundary
    def unresolved(self):
        return self.operations("unknown", include_suppressed=True)

    @boundary
    def list(self, include_suppressed=False):
        records.require(type(include_suppressed) is bool)
        control, rows, _ = self._inventory()
        result = []
        for row in rows:
            value = row["payload"]
            suppressed = self._suppressed(value, control)
            if value["media"] is not None and value["media"]["state"] != "linked" and not include_suppressed:
                continue
            if value["item"] is not None and (include_suppressed or not suppressed):
                item = {**value["item"], "consent_generation": value["consent_generation"],
                        "history_generation": value["history_generation"], "suppressed": suppressed}
                if value["media"] is not None and value["media"]["state"] == "linked":
                    item["photo_reference"] = copy.deepcopy(value["media"]["link"])
                result.append(item)
        return sorted(result, key=lambda item: (item["created_at"], item["session_id"]), reverse=True)

    @boundary
    def suppress(self, session_ids):
        records.require(type(session_ids) in (list, tuple, set, frozenset) and len(session_ids) <= 4096)
        identifiers = sorted({records.identifier(value) for value in session_ids})
        control = self._control()
        value = copy.deepcopy(control["payload"])
        value["suppressed"] = sorted(set(value["suppressed"]) | set(identifiers))
        if value != control["payload"]:
            self._write_control(control["revision"], value)
        return identifiers

    @boundary
    def clear(self):
        control = self._control()
        value = {**control["payload"], "clear_epoch": str(uuid4()), "generation": str(uuid4())}
        self._write_control(control["revision"], value)
        return True

    @boundary
    def suppressed_ids(self):
        control, rows, _ = self._inventory()
        return set(control["payload"]["suppressed"]) | {row["payload"]["session_id"] for row in rows if self._suppressed(row["payload"], control)}

    @boundary
    def is_suppressed(self, session_id):
        records.identifier(session_id)
        control = self._control()
        try:
            row = self._session(session_id)
        except WorkflowNotFound:
            row = None
        records.require(self._control()["revision"] == control["revision"], "control_changed")
        self._check()
        return (session_id in control["payload"]["suppressed"]
                or row is not None and self._suppressed(row["payload"], control))

    @boundary
    def mark_dispatching(self, operation_id):
        control, row, kind = self._find(operation_id)
        value = row["payload"]
        self._admission(value, control, dispatch=True)
        records.require(self._view(value, kind, control)["state"] == "queued", "dispatch_denied")
        value["operations"][kind].update(state="dispatching", owner=self._owner)
        self._write(row, control, value)
        return self.operation(operation_id)

    @boundary
    def mark_unknown(self, operation_id):
        control, row, kind = self._find(operation_id)
        records.require(self._view(row["payload"], kind, control)["state"] in {"dispatching", "unknown"}, "invalid_transition")
        row["payload"]["operations"][kind]["state"] = "unknown"
        self._write(row, control, row["payload"])
        return self.operation(operation_id)

    def _receipt(self, value, kind, control, memory_id, ready):
        records.token(memory_id)
        records.require(type(ready) is bool)
        operation = value["operations"][kind]
        records.require(self._view(value, kind, control)["state"] in {"dispatching", "unknown", "accepted", "recall_ready"}, "invalid_transition")
        records.require(operation["memory_id"] in (None, memory_id), "receipt_conflict")
        operation.update(memory_id=memory_id, state="recall_ready" if ready or operation["state"] == "recall_ready" else "accepted")
        if ready:
            operation["sage_status"] = "committed"

    @boundary
    def record_receipt(self, operation_id, memory_id, *, recall_ready=False):
        control, row, kind = self._find(operation_id)
        self._receipt(row["payload"], kind, control, memory_id, recall_ready)
        self._write(row, control, row["payload"])
        return self.operation(operation_id)

    @boundary
    def reconcile_unknown(self, operation_id, *, memory_id=None, proven_not_submitted=False, recall_ready=False):
        records.require(type(proven_not_submitted) is bool and type(recall_ready) is bool)
        records.require((memory_id is None) != (not proven_not_submitted) and not (memory_id is None and recall_ready))
        control, row, kind = self._find(operation_id)
        value = row["payload"]
        records.require(self._view(value, kind, control)["state"] == "unknown", "invalid_transition")
        operation = value["operations"][kind]
        if memory_id is not None:
            self._receipt(value, kind, control, memory_id, recall_ready)
        else:
            records.require(operation["journal_key"] is None, "reserved_operation_requires_reconciliation")
            permitted = control["payload"]["enabled"] and not self._suppressed(value, control) and value["dispatch_epoch"] == control["payload"]["dispatch_epoch"]
            operation.update(state="queued" if permitted else "cancelled", owner=None)
        self._write(row, control, value)
        return self.operation(operation_id)

    @boundary
    def mark_deprecated(self, operation_id, status):
        records.require(status in records.DEPRECATION)
        control, row, kind = self._find(operation_id)
        value = row["payload"]
        operation = value["operations"][kind]
        records.require(self._suppressed(value, control) and operation["memory_id"] is not None, "deprecation_denied")
        records.require(operation["deprecation_status"] != "deprecated" or status == "deprecated", "invalid_transition")
        operation["deprecation_status"] = status
        if status in {"challenged", "deprecated"}:
            operation.update(sage_status=status, state="accepted")
        self._write(row, control, value)
        return self.operation(operation_id)

    @contextmanager
    def bind_operation(self, operation_id):
        records.require(getattr(self._binding, "operation_id", None) is None, "already_bound")
        self.operation(operation_id)
        self._binding.operation_id = operation_id
        try:
            yield self
        finally:
            del self._binding.operation_id

    def _journal(self, key):
        records.digest(key)
        control, rows, _ = self._inventory()
        matches = [(row, kind) for row in rows for kind, operation in row["payload"]["operations"].items() if operation["journal_key"] == key]
        records.require(len(matches) <= 1, "journal_conflict")
        return (control, *matches[0]) if matches else (control, None, None)

    @boundary
    def reserve(self, key, fingerprint):
        records.digest(fingerprint)
        control, row, kind = self._journal(key)
        operation_id = getattr(self._binding, "operation_id", None)
        if row is not None:
            operation = row["payload"]["operations"][kind]
            records.require(operation["fingerprint"] == fingerprint and (operation_id is None or
                records.operation_id(row["payload"]["session_id"], kind) == operation_id), "journal_conflict")
            return False
        records.require(operation_id is not None, "operation_binding_required")
        control, row, kind = self._find(operation_id)
        value = row["payload"]
        self._admission(value, control, dispatch=True)
        operation = value["operations"][kind]
        records.require(self._view(value, kind, control)["state"] in {"queued", "dispatching"} and operation["journal_key"] is None, "dispatch_denied")
        operation.update(state="dispatching", owner=self._owner, journal_key=key, fingerprint=fingerprint)
        self._write(row, control, value)
        checked, stored, stored_kind = self._journal(key)
        records.require(stored is not None and records.operation_id(stored["payload"]["session_id"], stored_kind) == operation_id,
                        "journal_conflict")
        self._admission(stored["payload"], checked, dispatch=True)
        return True

    @boundary
    def get(self, key):
        _, row, kind = self._journal(key)
        records.require(row is not None, "journal_not_found")
        operation = row["payload"]["operations"][kind]
        return JournalEntry(operation["fingerprint"], None if operation["receipt"] is None else SimpleNamespace(**operation["receipt"]))

    @boundary
    def complete(self, key, receipt):
        value = records.validate_receipt(receipt, key)
        control, row, kind = self._journal(key)
        records.require(row is not None, "journal_not_found")
        operation = row["payload"]["operations"][kind]
        if operation["receipt"] is not None:
            previous = operation["receipt"]
            records.require(previous["memory_id"] == value["memory_id"], "receipt_conflict")
            for field in ("tx_hash", "committed_height", "committed"):
                if previous[field] is not None and previous[field] != "":
                    records.require(value[field] is None or value[field] == "" or value[field] == previous[field], "receipt_conflict")
                    value[field] = previous[field]
        ready = value["status"] == "committed" and value["projection_confirmed"] is True
        self._receipt(row["payload"], kind, control, value["memory_id"], ready)
        operation.update(receipt=value, sage_status=value["status"], state="recall_ready" if ready else "accepted")
        self._write(row, control, row["payload"])

    @boundary
    def prepare_media(self, ticket, session_id, link):
        link = records.validate_link(link)
        control, row, value = self._capture(ticket, records.identifier(session_id))
        records.require(value is not None, "media_denied")
        records.require(value["item"] is None or value["item"]["has_photo"], "media_conflict")
        if value["media"] is not None:
            records.require(value["media"]["link"] == link, "media_conflict")
            return copy.deepcopy(value["media"])
        value["media"] = dict(link=link, state="pending", intent_id=str(uuid4()))
        records.capacity(value, reserve_receipts=True)
        self._write(row, control, value)
        return copy.deepcopy(value["media"])

    @boundary
    def link_media(self, ticket, session_id, link):
        link = records.validate_link(link)
        control = self._control()
        records.require(ticket is not None and ticket == self._ticket(control), "media_denied")
        row = self._session(session_id)
        value = row["payload"]
        self._admission(value, control)
        records.require(value["media"] is not None and value["media"]["link"] == link, "media_conflict")
        value["media"]["state"] = "linked"
        self._write(row, control, value)
        return copy.deepcopy(link)

    @boundary
    def media(self, session_id, *, include_suppressed=False):
        records.require(type(include_suppressed) is bool)
        control = self._control()
        value = self._session(session_id)["payload"]
        if self._suppressed(value, control) and not include_suppressed:
            return None
        records.require(self._control()["revision"] == control["revision"], "control_changed")
        return copy.deepcopy(value["media"])

    @contextmanager
    def _call_guard(self, access_check, *, lifecycle=False):
        records.require(callable(access_check), "access_required")
        previous = getattr(self._binding, "access", None)
        base = self._access if previous is None else previous
        self._binding.access = access_check if lifecycle else lambda: base() is True and access_check() is True
        try:
            yield
        finally:
            if previous is None:
                del self._binding.access
            else:
                self._binding.access = previous

    def reserve_media(self, session_id, digest, *, ticket, item, access_check):
        """Durable text+media intent before caller PUT; no blob operation here."""
        with self._call_guard(access_check):
            return self._reserve_media(session_id, digest, ticket, item)

    @boundary
    def _reserve_media(self, session_id, digest, ticket, item):
        item = records.validate_item(item)
        records.require(item["session_id"] == records.identifier(session_id) and item["has_photo"] is True)
        link = dict(object_id=session_id, digest=records.digest(digest), backend="sage.private-media.v1")
        control, row, value = self._capture(ticket, session_id)
        records.require(value is not None and value["consent_generation"] == control["payload"]["generation"], "media_denied")
        if value["item"] is not None:
            records.require(value["item"] == item, "payload_conflict")
        else:
            value.update(item=item, item_hash=records.checksum(item), operations={kind: records.new_operation()
                for kind in ("interaction", "reflection") if kind == "interaction" or "reflection" in item})
        if value["media"] is not None:
            records.require(value["media"]["link"] == link, "media_conflict")
        else:
            records.require(all(operation["state"] == "queued" and operation["journal_key"] is None
                                for operation in value["operations"].values()), "media_conflict")
            value["media"] = dict(link=link, state="pending", intent_id=str(uuid4()))
        records.capacity(value, reserve_receipts=True)
        self._write(row, control, value)
        return records.MediaIntent(session_id, value["media"]["intent_id"], digest, value["consent_generation"],
                                   value["history_generation"], self._actor["chain_id"], self._actor["agent_id"])

    def commit_media(self, intent, *, access_check):
        """Caller has verified PUT digest; link only under the original consent."""
        with self._call_guard(access_check):
            return self._commit_media(intent)

    @boundary
    def _commit_media(self, intent):
        records.require(type(intent) is records.MediaIntent, "invalid_media_intent")
        records.require((intent.actor_chain_id, intent.actor_id) == (self._actor["chain_id"], self._actor["agent_id"]), "actor_changed")
        ticket = intent.store_generation if intent.history_generation is None else dict(
            store_generation=intent.store_generation, history_generation=intent.history_generation)
        control = self._control()
        records.require(ticket == self._ticket(control) and ticket is not None, "media_denied")
        row = self._session(intent.session_id)
        value = row["payload"]
        self._admission(value, control)
        link = dict(object_id=intent.session_id, digest=intent.digest, backend="sage.private-media.v1")
        records.require(value["media"] is not None and value["media"]["intent_id"] == intent.intent_id
                        and value["media"]["link"] == link and value["consent_generation"] == intent.store_generation
                        and value["history_generation"] == intent.history_generation, "media_conflict")
        if value["media"]["state"] != "linked":
            value["media"]["state"] = "linked"
            self._write(row, control, value)
        return True

    def pending_count(self):
        """Trusted producer-fenced lease only; unavailable evidence denies drain."""
        with self._call_guard(self._drain, lifecycle=True):
            return self._pending_count()

    def drain_snapshot(self):
        """Metadata only, under the genuine producer-fenced drain lease.

        Queued work is safe only after that external fence. Dispatching, unknown
        and pending media block drain. Never interprets unavailable as zero.
        """
        with self._call_guard(self._drain, lifecycle=True):
            return self._drain_snapshot()

    @boundary
    def _drain_snapshot(self):
        probe = self._workflow.storage
        before = probe.snapshot().epoch
        records.require(type(before) is str and bool(before), "invalid_storage_epoch")
        self._check()
        control = self._control()
        rows = self._workflow.scan(limit=50, max_records=4096, access_check=self._check_access)
        records.require(type(rows) is list and len(rows) <= 4096, "incomplete_inventory")
        identifiers = [records.identifier(row["record_id"]) for row in rows]
        records.require(len(set(identifiers)) == len(identifiers), "duplicate_inventory")
        controls = []
        counts = dict(queued=0, dispatching=0, unknown=0, pending_media=0)
        for row in rows:
            records.require(row["kind"] in KINDS and type(row["revision"]) is int
                            and 1 <= row["revision"] <= 2 ** 53 - 1, "invalid_inventory")
            if row["kind"] == "conversation_control":
                records.validate_control(row["payload"], self._actor)
                records.require(row == control, "control_changed")
                controls.append(row)
            elif row["kind"] == "conversation_session":
                self._validate_row(row)
                value = row["payload"]
                counts["pending_media"] += int(value["media"] is not None and value["media"]["state"] == "pending")
                for kind in value["operations"]:
                    state = self._view(value, kind, control)["state"]
                    if state in {"queued", "dispatching", "unknown"}:
                        counts[state] += 1
        records.require(len(controls) == 1, "incomplete_inventory")
        records.require(self._control() == control, "control_changed")
        self._check()
        after = probe.snapshot().epoch
        self._check()
        records.require(self._workflow.storage is probe and before == after, "storage_epoch_changed")
        return ConversationDrainSnapshot(before, **counts)

    @boundary
    def _pending_count(self):
        control, rows, _ = self._inventory()
        count = 0
        for row in rows:
            value = row["payload"]
            count += int(value["media"] is not None and value["media"]["state"] == "pending")
            for kind in value["operations"]:
                operation = self._view(value, kind, control)
                count += int(operation["state"] in {"queued", "dispatching", "unknown"})
        return count

    def close(self):
        self._closed = True
