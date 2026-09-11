from __future__ import annotations

import json
import math
import threading
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from .conversation_context import (
    ConversationEntry, VOICE_CONTEXT_BYTES, VOICE_CONVERSATION_BYTES,
    format_conversation_context, format_knowledge_context,
)
from .conversation_sage import ConversationSage, exclude_from_general_context
from .conversation_store import ConversationStore


def _store_activity(method):
    @wraps(method)
    def tracked(self, *arguments, **keywords):
        with self._activity():
            return method(self, *arguments, **keywords)
    return tracked


def _reflected_item(item):
    reflection = "An offline interaction completed. The user asked: " + item["question"][:1000]
    reflection += ". The assistant response is unverified; no user feedback or external verification has been received."
    return {**item, "reflection": reflection}


class _AdmissionClient:
    """Check SDK-call admission without cancelling already dispatched requests."""

    def __init__(self, client, require_admission):
        self._client = client
        self._require_admission = require_admission

    def __getattr__(self, name):
        attribute = getattr(self._client, name)
        if name not in {"get_profile", "domain_access_sample", "embed", "propose",
                        "get_memory", "list_memories", "hybrid", "forget"}:
            return attribute

        def admitted(*arguments, **keywords):
            self._require_admission()
            return attribute(*arguments, **keywords)
        return admitted


class _ConversationMedia:
    def __init__(self, service, provider):
        self._service = service
        self._provider = provider

    def _guard(self, access_check):
        def allowed():
            return self._service._admitted() and callable(access_check) and access_check() is True
        return allowed

    def put(self, object_id, jpeg, *, access_check):
        with self._service._activity():
            guard = self._guard(access_check)
            if guard() is not True:
                raise RuntimeError("Conversation media access unavailable")
            result = self._provider.put(object_id, jpeg, access_check=guard)
            try:
                if guard() is not True:
                    raise RuntimeError("Conversation media access unavailable")
            except Exception:
                from .private_media_store import ProtectedMediaOutcomeUnknown
                raise ProtectedMediaOutcomeUnknown() from None
            return result

    def get(self, object_id, *, access_check, expected_digest=None):
        with self._service._activity():
            guard = self._guard(access_check)
            if guard() is not True:
                raise RuntimeError("Conversation media access unavailable")
            result = self._provider.get(object_id, access_check=guard, expected_digest=expected_digest)
            if guard() is not True:
                raise RuntimeError("Conversation media access unavailable")
            return result


class _DeferredConversationStore:
    def __init__(self, factory, activity=None):
        self._factory = factory
        self._activity = activity
        self._store = None
        self._mutex = threading.RLock()
        self._closed = False

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def invoke(*arguments, **keywords):
            def call():
                with self._mutex:
                    if self._closed:
                        raise RuntimeError("Conversation store closed")
                    if self._store is None and name in {"pending_count", "drain_snapshot"}:
                        raise RuntimeError("Conversation storage has not been initialized")
                    if self._store is None:
                        self._store = self._factory()
                    return getattr(self._store, name)(*arguments, **keywords)
            if self._activity is None or name in {"pending_count", "drain_snapshot"}:
                return call()
            with self._activity():
                return call()
        return invoke

    def close(self):
        with self._mutex:
            if not self._closed:
                if self._store is not None:
                    self._store.close()
                self._closed = True


class ConversationService:
    def __init__(self, store, backend, *, media_store=None):
        self.store = store
        self.backend = backend
        self.stopped = threading.Event()
        self.worker = None
        self.last_error = None
        self.maintenance_cursor = 0
        self.reconcile_offsets = {}
        self.reconcile_cursor = 0
        self._lifecycle = threading.Condition()
        self._active_threads = {}
        self._closing = False
        self._closed = False
        self.protected_storage = False
        self._owned_client = None
        self._correction_attempts = None
        self._access_context = threading.local()
        self._epoch_reader = None
        self._owner = None
        self._drain_mutex = threading.RLock()
        self._drain_deadline = None
        self._drain_thread = None
        self._external_drain_check = None
        if media_store is not None and not all(callable(getattr(media_store, name, None)) for name in ("put", "get")):
            raise ValueError("Invalid conversation media provider")
        self.media_store = _ConversationMedia(self, media_store) if media_store is not None else None
        if backend is not None:
            backend.client = _AdmissionClient(backend.client, self._require_admission)

    def _admitted(self):
        if self.stopped.is_set():
            return False
        try:
            if self.protected_storage:
                if self._owner is None or self._owner.valid() is not True:
                    return False
                epoch = self._current_access_epoch()
                captured = getattr(self._access_context, "epoch", None)
                return epoch is not None and (captured is None or captured == epoch) and not self.stopped.is_set()
            from .device_access import device_unlocked
            return device_unlocked() is True and not self.stopped.is_set()
        except Exception:
            return False

    def correction_operation(self, operation, *, access_check, **arguments):
        if self._correction_attempts is None or operation not in {"submit", "finish", "reconcile"}:
            raise RuntimeError("Correction journal unavailable")
        with self._activity():
            def check():
                return access_check() is True and self._admitted() is True
            if check() is not True:
                raise RuntimeError("Correction access unavailable")
            return getattr(self._correction_attempts, operation)(access_check=check, **arguments)

    def _current_access_epoch(self):
        try:
            reader = self._epoch_reader
            if reader is None:
                from .device_access import access_epoch
                reader = access_epoch
            epoch = reader()
            if type(epoch) is str and len(epoch) == 32 and all(character in "0123456789abcdef" for character in epoch):
                return epoch
        except Exception:
            pass
        return None

    def _require_admission(self):
        if not self._admitted():
            raise RuntimeError("Conversation access unavailable")

    @contextmanager
    def _activity(self):
        identity = threading.get_ident()
        previous_epoch = getattr(self._access_context, "epoch", None)
        epoch = self._current_access_epoch() if self.protected_storage else None
        if self.protected_storage and (self._owner is None or self._owner.valid() is not True
                or epoch is None or previous_epoch is not None and previous_epoch != epoch):
            raise RuntimeError("Conversation access epoch unavailable")
        with self._lifecycle:
            if self.stopped.is_set() or self._closing or self._closed:
                raise RuntimeError("Conversation service stopped")
            self._active_threads[identity] = self._active_threads.get(identity, 0) + 1
            self._access_context.epoch = epoch
        try:
            yield
        finally:
            if previous_epoch is None:
                del self._access_context.epoch
            else:
                self._access_context.epoch = previous_epoch
            with self._lifecycle:
                self._active_threads[identity] -= 1
                if not self._active_threads[identity]:
                    del self._active_threads[identity]
                self._lifecycle.notify_all()

    @classmethod
    def from_config(cls, config, *, storage_lifecycle_check=None):
        from sage_sdk import AgentIdentity, SageClient
        if any(urlsplit(url).hostname not in {"localhost", "127.0.0.1", "::1"}
               for url in (config.sage_base_url, config.llama_base_url)):
            raise ValueError("Conversation memory requires local services")
        identity = AgentIdentity.from_file(str(config.sage_identity_path))
        if getattr(config, "protected_storage", False):
            from .conversation_workflow_store import ConversationWorkflowStore
            from .message_contacts import RecipientID
            from .message_storage import MessageStorageProbe
            from .private_media_store import ProtectedMediaStore
            from .protected_conversation_owner import ProtectedConversationOwner
            from .workflow_store import WorkflowStore

            actor = RecipientID(config.sage_chain_id, identity.agent_id)
            fence = ProtectedConversationOwner(Path(config.conversation_dir))
            client = None
            try:
                client = SageClient(base_url=config.sage_base_url, identity=identity, timeout=5, trust_env=False)
                probe = MessageStorageProbe(client, actor=actor, endpoint=config.sage_base_url)
                workflow = WorkflowStore(client, storage=probe)
                owner = []
                store = _DeferredConversationStore(lambda: ConversationWorkflowStore(
                    workflow, access_check=lambda: bool(owner) and owner[0]._admitted(),
                    drain_check=lambda: bool(owner) and owner[0]._drain_admitted()),
                    activity=lambda: owner[0]._activity())
                backend = ConversationSage(client, agent_id=identity.agent_id, domain=config.sage_domain,
                                           classification=1, journal=store,
                                           is_tombstoned=store.is_suppressed, ledger_consent=True)
                service = cls(store, backend, media_store=ProtectedMediaStore(client, storage=probe))
                owner.append(service)
                service.protected_storage = True
                from .correction_attempts import CorrectionAttempts
                service._correction_attempts = CorrectionAttempts(workflow)
                service._owned_client = client
                service._owner = fence
                service._external_drain_check = storage_lifecycle_check
                return service
            except Exception:
                if client is not None:
                    client.__exit__(None, None, None)
                fence.close()
                raise
        store = ConversationStore(Path(config.conversation_dir))
        client = SageClient(base_url=config.sage_base_url, identity=identity, timeout=8)
        backend = ConversationSage(client, agent_id=identity.agent_id, domain=config.sage_domain,
                                   classification=1, journal=store,
                                   is_tombstoned=store.is_suppressed, ledger_consent=True)
        return cls(store, backend)

    def start(self):
        with self._lifecycle:
            if self.stopped.is_set() or self._closing or self._closed:
                raise RuntimeError("Conversation service stopped")
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()

    def _run(self):
        startup_checked = False
        while not self.stopped.is_set():
            try:
                if not self._admitted():
                    self.stopped.wait(1)
                    continue
                if not startup_checked:
                    self.backend._authorize(write=True)
                    startup_checked = True
                self.tick()
                if not self._admitted():
                    continue
                self.last_error = "Memory saving needs reconciliation" if self.store.unresolved() else None
            except Exception:
                self.last_error = "SAGE memory needs attention"
            self.stopped.wait(3)

    def stop(self, timeout=None):
        """Drain admitted work; False means the caller must not close the store.

        Default waits for completion, not an arbitrary 12-second join. Explicit
        timeouts bound waiting only: receipts still persist after timeout/lock.
        Calling from active work requests stop but cannot join itself.
        """
        if timeout is not None and (type(timeout) not in (float, int)
                                    or not math.isfinite(timeout) or timeout < 0):
            raise ValueError("invalid_shutdown_timeout")
        deadline = None if timeout is None else time.monotonic() + timeout
        self.stopped.set()
        with self._lifecycle:
            worker = self.worker
            if threading.get_ident() in self._active_threads or worker is threading.current_thread():
                return False
        if worker is not None:
            worker.join(timeout=None if deadline is None else max(0, deadline - time.monotonic()))
            if worker.is_alive():
                return False
        with self._lifecycle:
            return self._lifecycle.wait_for(lambda: not self._active_threads,
                                           timeout=None if deadline is None else max(0, deadline - time.monotonic()))

    def _drain_admitted(self):
        with self._lifecycle:
            if (not self.protected_storage or self._closed or self._closing
                    or not self.stopped.is_set() or self._active_threads
                    or self.worker is not None and self.worker.is_alive()
                    or self._drain_thread != threading.get_ident()
                    or self._drain_deadline is None or time.monotonic() >= self._drain_deadline):
                return False
        try:
            return (self._owner is not None and self._owner.valid() is True
                    and (self._external_drain_check is None or self._external_drain_check() is True))
        except Exception:
            return False

    def drain_snapshot(self, timeout=5):
        """Fence and join producers, then inspect protected state before closing.

        The deadline is checked between bounded SDK requests; it cannot cancel
        a request already dispatched. This snapshot is not a reusable release
        permit. Ownership must remain held through the root release sequence.
        """
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("invalid_shutdown_timeout")
        from .conversation_records import ConversationDrainSnapshot
        deadline = time.monotonic() + timeout
        if not self._drain_mutex.acquire(timeout=timeout):
            raise RuntimeError("Conversation drain unavailable")
        try:
            if not self.stop(timeout=max(0, deadline - time.monotonic())):
                raise RuntimeError("Conversation drain unavailable")
            self._drain_deadline = deadline
            self._drain_thread = threading.get_ident()
            if self._drain_admitted() is not True:
                raise RuntimeError("Conversation drain unavailable")
            result = self.store.drain_snapshot()
            if (type(result) is not ConversationDrainSnapshot or type(result.storage_epoch) is not str
                    or not 1 <= len(result.storage_epoch) <= 256
                    or any(type(value) is not int or value < 0 for value in
                           (result.queued, result.dispatching, result.unknown, result.pending_media))
                    or self._drain_admitted() is not True):
                raise RuntimeError("Conversation drain unavailable")
            return result
        finally:
            self._drain_deadline = None
            self._drain_thread = None
            self._drain_mutex.release()

    def close(self, timeout=None):
        """Resource cleanup after joining; protected root release uses release()."""
        if timeout is not None and (type(timeout) not in (float, int)
                                    or not math.isfinite(timeout) or timeout < 0):
            raise ValueError("invalid_shutdown_timeout")
        deadline = None if timeout is None else time.monotonic() + timeout
        acquired = self._drain_mutex.acquire() if timeout is None else self._drain_mutex.acquire(timeout=timeout)
        if not acquired:
            return False
        try:
            if not self.stop(None if deadline is None else max(0, deadline - time.monotonic())):
                return False
            with self._lifecycle:
                self._closing = True
                if not self._closed:
                    self.store.close()
                    if self._owned_client is not None:
                        self._owned_client.__exit__(None, None, None)
                    if self._owner is not None:
                        self._owner.close()
                    self._closed = True
            return True
        finally:
            self._drain_mutex.release()

    def release(self, timeout=5):
        """One fenced inspection-and-close decision; unresolved work retains ownership."""
        if type(timeout) not in (float, int) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("invalid_shutdown_timeout")
        deadline = time.monotonic() + timeout
        if not self._drain_mutex.acquire(timeout=timeout):
            return False
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._closed:
                return False
            snapshot = self.drain_snapshot(timeout=remaining)
            if snapshot.dispatching or snapshot.unknown or snapshot.pending_media:
                return False
            return self.close(timeout=max(0, deadline - time.monotonic())) is True
        except Exception:
            return False
        finally:
            self._drain_mutex.release()

    @_store_activity
    def tick(self):
        if not self._admitted():
            return
        errors = []
        for operation in self.store.pending(limit=2):
            if not self._admitted():
                return
            with self.store.bind_operation(operation["operation_id"]):
                try:
                    method = self.backend.record_turn if operation["kind"] == "interaction" else self.backend.reflect
                    receipt = method(operation["session_id"], operation["kind"], operation["payload"])
                    if receipt.outcome == "unknown":
                        self.store.mark_unknown(operation["operation_id"])
                except Exception:
                    current = self.store.operation(operation["operation_id"])
                    if current["state"] == "dispatching":
                        self.store.mark_unknown(operation["operation_id"])
                    errors.append("Memory submission unavailable")
        if not self._admitted():
            return
        unresolved = self.store.unresolved()
        if unresolved:
            start = self.reconcile_cursor % len(unresolved)
            batch = (unresolved[start:] + unresolved[:start])[:2]
            self.reconcile_cursor += len(batch)
            for operation in batch:
                if not self._admitted():
                    return
                try:
                    with self.store.bind_operation(operation["operation_id"]):
                        receipt = self.backend.reconcile(operation["session_id"], operation["kind"], operation["payload"],
                                                        kind="turn" if operation["kind"] == "interaction" else "reflection")
                        offset = self.reconcile_offsets.get(operation["operation_id"], 0)
                        if receipt.outcome == "unknown" and offset:
                            if not self._admitted():
                                return
                            self.backend.reconcile(operation["session_id"], operation["kind"], operation["payload"],
                                                   kind="turn" if operation["kind"] == "interaction" else "reflection", offset=offset)
                except Exception:
                    errors.append("Memory reconciliation unavailable")
                finally:
                    self.reconcile_offsets[operation["operation_id"]] = (self.reconcile_offsets.get(operation["operation_id"], 0) + 50) % 7950
        if not self._admitted():
            return
        operations = [operation for operation in self.store.operations(include_suppressed=True)
                      if operation["memory_id"] and operation.get("deprecation_status") != "deprecated"
                      and (operation["suppressed"] or operation["state"] == "accepted")]
        if operations:
            start = self.maintenance_cursor % len(operations)
            batch = (operations[start:] + operations[:start])[:4]
            self.maintenance_cursor += len(batch)
            for operation in batch:
                if not self._admitted():
                    return
                try:
                    if operation["suppressed"]:
                        result = self.backend.forget(operation["session_id"], operation["memory_id"])
                        self.store.mark_deprecated(operation["operation_id"], result.memory_status or "unknown")
                    else:
                        record = self.backend.status(operation["session_id"], operation["memory_id"])
                        if record and record.status == "committed":
                            self.store.record_receipt(operation["operation_id"], operation["memory_id"], recall_ready=True)
                except Exception:
                    errors.append("Memory status update unavailable")
        if errors:
            raise RuntimeError(errors[0])

    @_store_activity
    def record(self, ticket, item):
        self._require_admission()
        return self.store.enqueue(ticket, _reflected_item(item))

    @_store_activity
    def reserve_media(self, session_id, digest, *, ticket, item, access_check):
        self._require_admission()
        if not callable(access_check) or access_check() is not True:
            raise RuntimeError("Conversation media access unavailable")
        intent = self.store.reserve_media(session_id, digest, ticket=ticket, item=_reflected_item(item),
                                          access_check=access_check)
        self._require_admission()
        if access_check() is not True:
            raise RuntimeError("Conversation media access unavailable")
        return intent

    @_store_activity
    def commit_media(self, intent, *, access_check):
        self._require_admission()
        if not callable(access_check) or access_check() is not True:
            raise RuntimeError("Conversation media access unavailable")
        result = self.store.commit_media(intent, access_check=access_check)
        self._require_admission()
        if access_check() is not True or result is not True:
            raise RuntimeError("Conversation media commit unavailable")
        return True

    @_store_activity
    def items(self):
        result = []
        for item in self.store.list():
            operation = self.store.work(item["session_id"], "interaction")
            state = operation["state"]
            result.append({"session_id": item["session_id"], "created_at": item["created_at"],
                           "transcript": item["question"], "response": item["answer"],
                           "has_photo": item["has_photo"], "memory_state": state,
                           **{field: item[field] for field in ('thread_id', 'supersedes') if field in item},
                           **({"photo_reference": dict(item["photo_reference"])} if item.get("photo_reference") else {}),
                           "memory_id": operation["memory_id"]})
        return result

    @_store_activity
    def context(self, query):
        if not isinstance(query, str) or not query.strip() or len(query.encode()) > 16384:
            raise ValueError("invalid_query")
        self._require_admission()
        suppressed = self.store.suppressed_ids()
        memories, embedding = self.backend.recall_conversations_with_embedding(
            query, suppressed_session_ids=suppressed, limit=3, recent_fallback=True)
        entries = []
        entry_sessions = {}
        for memory in memories:
            if self.store.is_suppressed(memory.session_id):
                continue
            payload = json.loads(memory.text)
            if (not isinstance(payload, dict) or payload.get("version") != 1
                    or payload.get("session_id") != memory.session_id
                    or (payload.get("kind"), memory.kind) not in {("interaction", "turn"), ("reflection", "reflection")}):
                raise ValueError("invalid_conversation_payload")
            if payload.get("kind") == "interaction":
                entries.extend([
                    ConversationEntry(memory.memory_id + ":user", payload["created_at"], "user", payload["question"]),
                    ConversationEntry(memory.memory_id + ":assistant", payload["created_at"], "assistant", payload["answer"]),
                ])
                entry_sessions[memory.memory_id + ":user"] = memory.session_id
                entry_sessions[memory.memory_id + ":assistant"] = memory.session_id
            elif payload.get("kind") == "reflection":
                entries.append(ConversationEntry(memory.memory_id, payload["created_at"], "assistant",
                                                 payload["reflection"], kind="reflection"))
                entry_sessions[memory.memory_id] = memory.session_id
        knowledge = self.backend.client.hybrid(query=query, embedding=embedding, domain_tag=self.backend.domain,
                                              top_k=8, status_filter="committed")
        records = [record for record in knowledge.results
                  if not exclude_from_general_context(record)
                  and str(getattr(record.status, "value", record.status)) == "committed"]
        latest_suppressed = self.store.suppressed_ids()
        entries = [entry for entry in entries if entry_sessions[entry.record_id] not in latest_suppressed]
        result = []
        if entries:
            result.append(format_conversation_context(entries, max_turns=6, max_chars=VOICE_CONVERSATION_BYTES))
        remaining = VOICE_CONTEXT_BYTES - sum(len(part.encode("utf-8")) for part in result)
        knowledge_context = format_knowledge_context(records, domain=self.backend.domain, max_bytes=remaining)
        if knowledge_context:
            result.insert(0, knowledge_context)
        self._require_admission()
        return result
