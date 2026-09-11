import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sage_lantern.conversation_sage import ConversationSage, PROVIDER
from sage_lantern.conversation_service import ConversationService
from sage_lantern.conversation_store import ConversationStore


class FakeConversationSDK:
    def __init__(self, store):
        self.store = store
        self.records = {}
        self.hidden = set()
        self.calls = []
        self.timeout_after_submit = False
        self.before_response = None
        self.knowledge = []
        self.clearance = 1

    def _call(self, name, arguments):
        if self.store._db.in_transaction or self.store._mutex._is_owned():
            raise AssertionError("network call holds conversation storage lock")
        self.calls.append((name, arguments))
        if self.before_response is not None:
            self.before_response(name, arguments)

    def get_profile(self):
        self._call("get_profile", {})
        return SimpleNamespace(agent_id="private-agent", role="member", profile="companion",
                               home_domain="local-field-knowledge", enrollment_status="active",
                               registration_status="active", approval_required=False,
                               can_read=True, can_write=True, clearance=self.clearance)

    def domain_access_sample(self):
        self._call("domain_access_sample", {})
        return SimpleNamespace(owned_domains=["local-field-knowledge"],
                               readable_domains=["local-field-knowledge"],
                               writable_domains=["local-field-knowledge"], truncated=True)

    def embed(self, text):
        self._call("embed", dict(text=text))
        return [0.25, 0.75]

    def propose(self, content, memory_type, domain_tag, confidence, embedding=None,
                classification=None, provider=None):
        self._call("propose", dict(content=content, memory_type=memory_type,
                                   domain_tag=domain_tag, confidence=confidence,
                                   embedding=embedding, classification=classification, provider=provider))
        memory_id = str(uuid4())
        self.records[memory_id] = SimpleNamespace(
            memory_id=memory_id, content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            submitting_agent="private-agent", memory_type=memory_type, domain_tag=domain_tag,
            classification=classification, provider=provider, status="proposed",
            confidence_score=confidence, created_at="2026-09-05T10:00:00Z")
        if self.timeout_after_submit:
            self.hidden.add(memory_id)
            raise TimeoutError("private SDK response body must not escape")
        return SimpleNamespace(memory_id=memory_id, tx_hash="tx-" + memory_id,
                               status="proposed", committed=True, committed_height=4488,
                               projection_confirmed=False, retryable=False)

    def _visible(self, domain, provider=None, status=None):
        return [record for record in reversed(list(self.records.values()))
                if record.memory_id not in self.hidden and record.domain_tag == domain
                and (provider is None or record.provider == provider)
                and (status is None or record.status == status)]

    def list_memories(self, limit=50, offset=0, domain=None, tag=None, provider=None,
                      status=None, sort=None, agent=None):
        self._call("list_memories", dict(limit=limit, offset=offset, domain=domain,
                                        provider=provider, status=status, agent=agent))
        records = self._visible(domain, provider, status)
        if agent is not None:
            records = [record for record in records if record.submitting_agent == agent]
        return SimpleNamespace(memories=records[offset:offset + limit],
                               has_more=offset + limit < len(records))

    def hybrid(self, query, embedding, domain_tag=None, top_k=10, status_filter=None,
               min_confidence=None, provider=None, tags=None, expansions=None):
        self._call("hybrid", dict(query=query, embedding=embedding, domain_tag=domain_tag,
                                 provider=provider, top_k=top_k, status_filter=status_filter))
        records = self._visible(domain_tag, provider, status_filter)
        if provider is None:
            records = self.knowledge + records
        return SimpleNamespace(results=records[:top_k])

    def get_memory(self, memory_id):
        self._call("get_memory", dict(memory_id=memory_id))
        if memory_id in self.hidden:
            raise LookupError("projection unavailable")
        return self.records[memory_id]

    def forget(self, memory_id, reason=None):
        self._call("forget", dict(memory_id=memory_id, reason=reason))
        self.records[memory_id].status = "deprecated"
        return dict(status="deprecated", tx_hash="withdraw-" + memory_id)

    def commit_all(self):
        for record in self.records.values():
            record.status = "committed"

    def count(self, method):
        return sum(name == method for name, arguments in self.calls)


class ConversationServiceTests(unittest.TestCase):
    def setUp(self):
        self.unlocked = True
        self.gate_checked = threading.Event()
        def admission():
            self.gate_checked.set()
            return self.unlocked
        gate_patch = patch("sage_lantern.device_access.device_unlocked", side_effect=admission)
        self.gate = gate_patch.start()
        self.addCleanup(gate_patch.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve() / "conversations"
        self.store = ConversationStore(self.directory)
        self.addCleanup(lambda: self.store.close())
        self.client = FakeConversationSDK(self.store)
        self.service = self.make_service()
        self.item = dict(session_id=str(uuid4()), created_at="2026-09-05T10:00:00Z",
                         question="Where did I put my camera?", answer="You said it was on your desk.",
                         has_photo=False)

    def make_service(self):
        backend = ConversationSage(self.client, agent_id="private-agent", domain="local-field-knowledge",
                                   classification=1, journal=self.store,
                                   is_tombstoned=self.store.is_suppressed, ledger_consent=True)
        return ConversationService(self.store, backend)

    def enqueue(self, item=None):
        return self.service.record(self.store.approve(True), self.item if item is None else item)

    def reopen(self):
        self.store.close()
        self.store = ConversationStore(self.directory)
        self.client.store = self.store
        self.service = self.make_service()

    def committed_session(self):
        operation_ids = self.enqueue()
        self.service.tick()
        self.client.commit_all()
        self.service.tick()
        return operation_ids

    def context_entries(self, query="Where was that camera again?"):
        context = self.service.context(query)
        envelopes = []
        for part in context:
            try:
                value = json.loads(part)
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and value.get("schema") == "sage.lantern.conversation-context.v1":
                envelopes.append(value)
        return [entry for envelope in envelopes for entry in envelope["entries"]]

    def test_enqueue_two_deterministic_payloads_without_network(self):
        identifiers = self.enqueue()
        self.assertEqual(len(identifiers), 2)
        self.assertEqual(self.enqueue(), identifiers)
        self.assertEqual(self.client.calls, [])
        pending = self.store.pending()
        self.assertEqual({operation["kind"] for operation in pending}, {"interaction", "reflection"})
        for operation in pending:
            payload = json.loads(operation["payload"])
            self.assertEqual(payload["version"], 1)
            self.assertEqual(payload["kind"], operation["kind"])
            self.assertEqual(payload["session_id"], self.item["session_id"])
            self.assertEqual(payload["created_at"], self.item["created_at"])
            self.assertEqual(operation["payload_sha256"], hashlib.sha256(operation["payload"].encode()).hexdigest())

    def test_without_consent_record_and_tick_do_not_dispatch(self):
        self.assertEqual(self.service.record(None, self.item), [])
        self.service.tick()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.service.items(), [])

    def test_dispatch_two_ops_acceptance_is_not_semantic_commit(self):
        identifiers = self.enqueue()
        self.service.tick()
        self.assertEqual(self.client.count("propose"), 2)
        self.assertEqual({self.store.operation(identifier)["state"] for identifier in identifiers}, {"accepted"})
        self.assertEqual(self.context_entries(), [])
        self.assertEqual(self.service.items()[0]["memory_state"], "accepted")
        for name, arguments in self.client.calls:
            if name == "propose":
                self.assertEqual(arguments["classification"], 1)
                self.assertEqual(arguments["embedding"], [0.25, 0.75])
                self.assertEqual(arguments["provider"], PROVIDER)
                outer = json.loads(arguments["content"])
                inner = json.loads(outer["text"])
                self.assertEqual(outer["event_id"], inner["kind"])
                self.assertEqual(outer["kind"], "turn" if inner["kind"] == "interaction" else "reflection")

    def test_poll_exact_ids_promotes_only_committed_projection(self):
        identifiers = self.enqueue()
        self.service.tick()
        before = self.client.count("get_memory")
        self.client.commit_all()
        self.service.tick()
        self.assertEqual(self.client.count("get_memory") - before, 2)
        self.assertEqual({self.store.operation(identifier)["state"] for identifier in identifiers}, {"recall_ready"})
        self.assertEqual(self.client.count("propose"), 2)

    def test_cross_session_context_maps_store_version_one_and_attribution(self):
        self.committed_session()
        new_session = {**self.item, "session_id": str(uuid4()), "created_at": "2026-09-06T10:00:00Z",
                       "question": "Where was that again?", "answer": "Checking prior conversation."}
        self.enqueue(new_session)
        entries = self.context_entries()
        self.assertEqual(len(entries), 3)
        interaction = self.store.work(self.item["session_id"], "interaction")
        user = next(entry for entry in entries if entry["speaker"] == "user")
        assistant = next(entry for entry in entries if entry["speaker"] == "assistant" and entry["kind"] == "turn")
        reflection = next(entry for entry in entries if entry["kind"] == "reflection")
        self.assertEqual(user["text"], self.item["question"])
        self.assertEqual(assistant["text"], self.item["answer"])
        self.assertEqual(user["record_id"], interaction["memory_id"] + ":user")
        self.assertEqual(user["created_at"], self.item["created_at"])
        self.assertEqual(user["attribution"], "user_statement_not_verified_fact")
        self.assertEqual(assistant["attribution"], "prior_assistant_output_unverified")
        self.assertIn("unverified", reflection["text"])
        self.assertFalse(any(entry["text"] == new_session["question"] for entry in entries))

    def test_suppressed_queued_session_never_dispatches_or_appears(self):
        self.enqueue()
        self.store.suppress([self.item["session_id"]])
        self.service.tick()
        self.assertEqual(self.client.count("propose"), 0)
        self.assertEqual(self.service.items(), [])
        self.assertEqual(self.context_entries(), [])

    def test_suppression_hides_committed_context_even_before_withdrawal(self):
        self.committed_session()
        self.store.suppress([self.item["session_id"]])
        self.assertEqual(self.context_entries(), [])
        self.assertEqual(self.service.items(), [])
        self.assertTrue(all(record.status == "committed" for record in self.client.records.values()))

    def test_withdraw_both_uses_memory_status_and_persists_tombstone(self):
        identifiers = self.committed_session()
        self.store.suppress([self.item["session_id"]])
        self.service.tick()
        self.assertEqual(self.client.count("forget"), 2)
        for identifier in identifiers:
            self.assertEqual(self.store.operation(identifier)["deprecation_status"], "deprecated")
        self.reopen()
        self.assertTrue(self.store.is_suppressed(self.item["session_id"]))
        self.assertEqual(self.context_entries(), [])
        self.service.tick()
        self.assertEqual(self.client.count("forget"), 2)

    def test_unknown_submission_recovers_after_restart_without_resubmit(self):
        identifiers = self.enqueue()
        self.client.timeout_after_submit = True
        self.service.tick()
        self.assertEqual({self.store.operation(identifier)["state"] for identifier in identifiers}, {"unknown"})
        self.assertEqual(self.client.count("propose"), 2)
        self.reopen()
        self.service.tick()
        self.assertEqual(self.client.count("propose"), 2)
        self.client.hidden.clear()
        self.client.commit_all()
        for attempt in range(160):
            self.service.tick()
            if all(self.store.operation(identifier)["state"] == "recall_ready" for identifier in identifiers):
                break
        self.assertEqual({self.store.operation(identifier)["state"] for identifier in identifiers}, {"recall_ready"})
        self.assertEqual(self.client.count("propose"), 2)
        self.assertEqual(len(self.context_entries()), 3)

    def test_reconciliation_absence_never_requeues_unknown_work(self):
        identifiers = self.enqueue()
        self.client.timeout_after_submit = True
        self.service.tick()
        for iteration in range(3):
            self.service.tick()
        self.assertEqual(self.client.count("propose"), 2)
        self.assertEqual({self.store.operation(identifier)["state"] for identifier in identifiers}, {"unknown"})
        self.assertEqual(self.store.pending(), [])

    def test_unknown_suppressed_work_reconciles_for_withdrawal_not_context(self):
        identifiers = self.enqueue()
        self.client.timeout_after_submit = True
        self.service.tick()
        self.store.suppress([self.item["session_id"]])
        self.reopen()
        self.client.hidden.clear()
        self.client.commit_all()
        self.service.tick()
        self.assertEqual(self.client.count("propose"), 2)
        self.assertEqual(self.client.count("forget"), 2)
        self.assertEqual(self.context_entries(), [])
        self.assertTrue(all(self.store.operation(identifier)["deprecation_status"] == "deprecated"
                            for identifier in identifiers))

    def test_dispatch_per_tick_is_bounded_to_two_operations(self):
        self.enqueue()
        self.enqueue({**self.item, "session_id": str(uuid4())})
        self.service.tick()
        self.assertEqual(self.client.count("propose"), 2)
        self.assertEqual(len(self.store.pending()), 2)

    def test_unavailable_private_grants_fail_closed_before_embed_or_submit(self):
        self.enqueue()
        self.client.clearance = 0
        with self.assertRaises(Exception):
            self.service.tick()
        self.assertEqual(self.client.count("embed"), 0)
        self.assertEqual(self.client.count("propose"), 0)

    def test_context_rejects_unknown_payload_version(self):
        self.committed_session()
        for record in self.client.records.values():
            outer = json.loads(record.content)
            payload = json.loads(outer["text"])
            payload["version"] = 999
            outer["text"] = json.dumps(payload)
            record.content = json.dumps(outer)
            record.content_hash = hashlib.sha256(record.content.encode()).hexdigest()
        try:
            entries = self.context_entries()
        except ValueError:
            return
        self.assertEqual(entries, [], "unknown payload versions must not be treated as version one")

    def test_context_rechecks_tombstone_after_final_knowledge_network_call(self):
        self.committed_session()
        def delete_during_knowledge(name, arguments):
            if name == "hybrid" and arguments["provider"] is None:
                self.store.suppress([self.item["session_id"]])
        self.client.before_response = delete_during_knowledge
        try:
            entries = self.context_entries()
        except RuntimeError as error:
            self.assertEqual(str(error), "Conversation recall changed; retry")
            return
        self.assertEqual(entries, [], "deletion during the final network call must suppress context")

    def test_context_reuses_only_current_request_embedding(self):
        self.committed_session()
        self.client.calls.clear()
        self.context_entries("first question")
        self.assertEqual(self.client.count("embed"), 1)
        self.assertEqual(self.client.count("hybrid"), 2)
        self.context_entries("second question")
        self.assertEqual(self.client.count("embed"), 2)
        self.assertEqual(self.client.count("hybrid"), 4)

    def test_context_budget_does_not_change_query_and_bounds_recent_fallback(self):
        self.committed_session()
        self.client.calls.clear()
        query = "أين الكاميرا؟ Keep this query exactly."
        self.client.knowledge = [SimpleNamespace(memory_id=str(uuid4()), provider="facts",
            domain_tag="local-field-knowledge", status="committed", created_at="2026-09-07T00:00:00Z",
            content="bounded observation " * 40) for _ in range(8)]
        with patch.object(self.service.backend, "recall_conversations_with_embedding",
                          wraps=self.service.backend.recall_conversations_with_embedding) as recall, \
                patch.object(self.client, "hybrid", wraps=self.client.hybrid) as hybrid:
            context = self.service.context(query)
        self.assertLessEqual(sum(len(part.encode()) for part in context), 3072)
        recall.assert_called_once_with(query, suppressed_session_ids=set(), limit=3, recent_fallback=True)
        self.assertEqual([call.kwargs["query"] for call in hybrid.call_args_list], [query, query])

    def test_recent_fallback_resumes_without_hybrid_matches(self):
        self.committed_session()
        for query in ("Where was that again?", "أين كانت مرة أخرى؟"):
            with self.subTest(query=query), \
                    patch.object(self.client, "hybrid", return_value=SimpleNamespace(results=[])), \
                    patch.object(self.client, "list_memories", wraps=self.client.list_memories) as recent:
                context = self.service.context(query)
            self.assertLessEqual(sum(len(part.encode()) for part in context), 3072)
            entries = json.loads(context[0])["entries"]
            self.assertTrue(any(entry["speaker"] == "user" and entry["text"] == self.item["question"]
                                for entry in entries))
            self.assertTrue(any(entry["speaker"] == "assistant" and entry["text"] == self.item["answer"]
                                and entry["attribution"] == "prior_assistant_output_unverified" for entry in entries))
            recent.assert_called_once_with(limit=3, offset=0, domain=self.service.backend.domain,
                provider=self.client.records[next(iter(self.client.records))].provider,
                agent="private-agent", sort="newest", status="committed")

    def test_recent_fallback_rechecks_suppression_after_knowledge(self):
        self.committed_session()

        def suppress_and_return(**arguments):
            if arguments.get("provider") is None:
                self.store.suppress([self.item["session_id"]])
            return SimpleNamespace(results=[])

        with patch.object(self.client, "hybrid", side_effect=suppress_and_return):
            self.assertEqual(self.context_entries("Where was that again?"), [])

    def test_recent_fallback_cannot_disclose_after_lock_or_epoch_change(self):
        self.committed_session()
        epoch = {"value": "a" * 32}
        self.service.protected_storage = True
        self.service._owner = SimpleNamespace(valid=lambda: True)
        self.service._epoch_reader = lambda: epoch["value"]
        original = self.client.list_memories

        for change in ("lock", "epoch"):
            self.unlocked = True
            epoch["value"] = "a" * 32

            def changed(**arguments):
                result = original(**arguments)
                if change == "lock":
                    self.unlocked = False
                    epoch["value"] = None
                else:
                    epoch["value"] = "b" * 32
                return result

            with self.subTest(change=change), \
                    patch.object(self.client, "hybrid", return_value=SimpleNamespace(results=[])), \
                    patch.object(self.client, "list_memories", side_effect=changed):
                with self.assertRaises(RuntimeError):
                    self.service.context("Where was that again?")

    def test_context_rejects_invalid_shared_embedding(self):
        with patch.object(self.client, "embed", return_value=[float("nan")]):
            with self.assertRaises(RuntimeError):
                self.service.context("question")
        self.assertEqual(self.client.count("hybrid"), 0)

    def test_shared_embedding_does_not_bypass_final_revocation(self):
        def revoke(name, arguments):
            if name == "hybrid" and arguments["provider"] is None:
                self.unlocked = False

        self.client.before_response = revoke
        with self.assertRaises(RuntimeError):
            self.service.context("question")
        self.assertEqual(self.client.count("embed"), 1)

    def test_shared_embedding_does_not_adopt_changed_access_epoch(self):
        epoch = {"value": "a" * 32}
        self.service.protected_storage = True
        self.service._owner = SimpleNamespace(valid=lambda: True)
        self.service._epoch_reader = lambda: epoch["value"]

        def change_epoch(name, arguments):
            if name == "hybrid" and arguments["provider"] is None:
                epoch["value"] = "b" * 32

        self.client.before_response = change_epoch
        with self.assertRaises(RuntimeError):
            self.service.context("question")
        self.assertEqual(self.client.count("embed"), 1)

    def test_context_rejects_inner_session_mismatch(self):
        self.committed_session()
        for record in self.client.records.values():
            outer = json.loads(record.content)
            payload = json.loads(outer["text"])
            payload["session_id"] = str(uuid4())
            outer["text"] = json.dumps(payload)
            record.content = json.dumps(outer)
            record.content_hash = hashlib.sha256(record.content.encode()).hexdigest()
        try:
            entries = self.context_entries()
        except ValueError:
            return
        self.assertEqual(entries, [], "inner session identity must match the verified adapter envelope")

    def test_context_rejects_reflection_disguised_as_interaction(self):
        self.committed_session()
        for record in self.client.records.values():
            outer = json.loads(record.content)
            if outer["kind"] == "reflection":
                payload = json.loads(outer["text"])
                payload.update(kind="interaction", question="fabricated user statement",
                               answer="fabricated assistant answer", has_photo=False)
                outer["text"] = json.dumps(payload)
                record.content = json.dumps(outer)
                record.content_hash = hashlib.sha256(record.content.encode()).hexdigest()
        try:
            entries = self.context_entries()
        except ValueError:
            return
        self.assertFalse(any(entry["text"] == "fabricated user statement" for entry in entries),
                         "inner kind must match turn/reflection attribution in the outer envelope")

    def test_startup_locked_makes_no_store_or_backend_calls_and_stops_promptly(self):
        self.unlocked = False
        with patch.object(self.store, "pending", wraps=self.store.pending) as pending, \
                patch.object(self.store, "unresolved", wraps=self.store.unresolved) as unresolved:
            self.service.start()
            try:
                self.assertTrue(self.gate_checked.wait(1))
                self.assertTrue(self.service.stop(timeout=1))
                self.assertEqual(self.client.calls, [])
                pending.assert_not_called()
                unresolved.assert_not_called()
            finally:
                self.service.stop(timeout=2)

    def test_direct_tick_locked_or_gate_failure_does_not_dispatch(self):
        identifiers = self.enqueue()
        self.unlocked = False
        self.service.tick()
        self.gate.side_effect = RuntimeError("auth service unavailable")
        self.service.tick()
        self.assertEqual(self.client.calls, [])
        self.assertEqual({self.store.operation(identifier)["state"] for identifier in identifiers}, {"queued"})

    def test_lock_mid_first_submit_preserves_receipt_and_blocks_second(self):
        identifiers = self.enqueue()
        def lock_during_submit(name, arguments):
            if name == "propose":
                self.unlocked = False
        self.client.before_response = lock_during_submit
        self.service.tick()
        self.assertEqual(self.client.count("propose"), 1)
        self.assertEqual(self.store.operation(identifiers[0])["state"], "accepted")
        self.assertIsNotNone(self.store.operation(identifiers[0])["memory_id"])
        self.assertEqual(self.store.operation(identifiers[1])["state"], "queued")
        self.assertEqual(self.client.count("list_memories"), 0)
        self.assertEqual(self.client.count("get_memory"), 0)
        self.client.before_response = None
        self.unlocked = True
        self.service.tick()
        self.assertEqual(self.client.count("propose"), 2)

    def test_lock_between_embedding_and_propose_prevents_sdk_mutation(self):
        identifiers = self.enqueue()
        def lock_during_embedding(name, arguments):
            if name == "embed":
                self.unlocked = False
        self.client.before_response = lock_during_embedding
        self.service.tick()
        self.assertEqual(self.client.count("propose"), 0)
        self.assertEqual(self.store.operation(identifiers[0])["state"], "unknown")
        self.assertEqual(self.store.operation(identifiers[1])["state"], "queued")

    def test_lock_after_ambiguous_submit_preserves_unknown_without_more_work(self):
        identifiers = self.enqueue()
        self.client.timeout_after_submit = True
        def lock_during_submit(name, arguments):
            if name == "propose":
                self.unlocked = False
        self.client.before_response = lock_during_submit
        self.service.tick()
        self.assertEqual(self.client.count("propose"), 1)
        self.assertEqual(self.store.operation(identifiers[0])["state"], "unknown")
        self.assertEqual(self.store.operation(identifiers[1])["state"], "queued")
        self.assertEqual(self.client.count("list_memories"), 0)

    def test_lock_during_reconcile_preserves_evidence_and_defers_next_operation(self):
        identifiers = self.enqueue()
        self.client.timeout_after_submit = True
        self.service.tick()
        self.client.hidden.clear()
        self.client.commit_all()
        self.client.calls.clear()
        def lock_during_list(name, arguments):
            if name == "list_memories":
                self.unlocked = False
        self.client.before_response = lock_during_list
        self.service.tick()
        self.assertEqual(self.client.count("list_memories"), 1)
        self.assertEqual({self.store.operation(identifier)["state"] for identifier in identifiers},
                         {"unknown", "recall_ready"})
        self.assertEqual(self.client.count("get_memory"), 0)

    def test_lock_before_reconcile_second_page_defers_page(self):
        identifiers = self.enqueue()
        self.client.timeout_after_submit = True
        self.service.tick()
        self.client.calls.clear()
        self.service.reconcile_offsets = {identifier: 50 for identifier in identifiers}
        def lock_during_list(name, arguments):
            if name == "list_memories":
                self.unlocked = False
        self.client.before_response = lock_during_list
        self.service.tick()
        self.assertEqual(self.client.count("list_memories"), 1)
        self.assertEqual({self.store.operation(identifier)["state"] for identifier in identifiers}, {"unknown"})

    def test_lock_during_forget_preserves_deprecation_receipt_and_defers_next(self):
        identifiers = self.committed_session()
        self.store.suppress([self.item["session_id"]])
        def lock_during_forget(name, arguments):
            if name == "forget":
                self.unlocked = False
        self.client.before_response = lock_during_forget
        self.service.tick()
        self.assertEqual(self.client.count("forget"), 1)
        statuses = [self.store.operation(identifier)["deprecation_status"] for identifier in identifiers]
        self.assertEqual(statuses.count("deprecated"), 1)

    def test_lock_between_get_and_forget_blocks_mutation(self):
        self.committed_session()
        self.store.suppress([self.item["session_id"]])
        def lock_during_get(name, arguments):
            if name == "get_memory":
                self.unlocked = False
        self.client.before_response = lock_during_get
        self.service.tick()
        self.assertEqual(self.client.count("forget"), 0)
        self.assertTrue(self.store.is_suppressed(self.item["session_id"]))

    def test_start_is_idempotent_and_stopped_service_cannot_restart(self):
        self.unlocked = False
        self.service.start()
        first = self.service.worker
        self.service.start()
        self.assertIs(self.service.worker, first)
        self.assertTrue(self.service.stop(timeout=2))
        with self.assertRaisesRegex(RuntimeError, "service stopped"):
            self.service.start()

    def test_timed_close_keeps_store_open_until_inflight_worker_receipt_finishes(self):
        identifiers = self.enqueue()
        entered = threading.Event()
        release = threading.Event()
        def block_submit(name, arguments):
            if name == "propose":
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test release missing")
        self.client.before_response = block_submit
        self.service.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(self.service.close(timeout=0.01))
            self.assertFalse(self.store._closed)
            self.assertEqual(self.store.operation(identifiers[0])["state"], "dispatching")
            release.set()
            self.assertTrue(self.service.stop(timeout=2))
            self.assertFalse(self.service.worker.is_alive())
            self.assertEqual(self.store.operation(identifiers[0])["state"], "accepted")
            self.assertEqual(self.store.operation(identifiers[1])["state"], "queued")
            self.assertEqual(self.client.count("propose"), 1)
            self.assertTrue(self.service.close(timeout=1))
            self.assertTrue(self.store._closed)
            self.assertTrue(self.service.close(timeout=0))
        finally:
            release.set()
            self.service.stop(timeout=3)

    def test_default_stop_waits_for_direct_tick_then_store_can_close(self):
        identifiers = self.enqueue()
        entered = threading.Event()
        release = threading.Event()
        drained = threading.Event()
        errors = []
        def block_submit(name, arguments):
            if name == "propose":
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test release missing")
        def tick():
            try:
                self.service.tick()
            except Exception as error:
                errors.append(error)
        def drain():
            if self.service.stop():
                drained.set()
        self.client.before_response = block_submit
        worker = threading.Thread(target=tick)
        stopper = threading.Thread(target=drain)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            stopper.start()
            self.assertTrue(self.service.stopped.wait(1))
            self.assertFalse(drained.is_set())
            self.assertFalse(self.store._closed)
            release.set()
            worker.join(2)
            stopper.join(2)
            self.assertTrue(drained.is_set())
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(self.store.operation(identifiers[0])["state"], "accepted")
            self.assertTrue(self.service.close())
        finally:
            release.set()
            worker.join(3)
            if stopper.ident is not None:
                stopper.join(3)

    def test_close_from_inflight_work_never_self_joins_or_closes_store(self):
        identifiers = self.enqueue()
        results = []
        def request_close(name, arguments):
            if name == "propose":
                results.append(self.service.close(timeout=0))
        self.client.before_response = request_close
        self.service.tick()
        self.assertEqual(results, [False])
        self.assertFalse(self.store._closed)
        self.assertEqual(self.store.operation(identifiers[0])["state"], "accepted")
        self.assertEqual(self.client.count("propose"), 1)
        self.assertTrue(self.service.close())

    def test_locked_record_and_context_fail_before_storage_or_sdk_work(self):
        ticket = self.store.approve(True)
        self.unlocked = False
        with patch.object(self.store, "enqueue", wraps=self.store.enqueue) as enqueue, \
                patch.object(self.store, "suppressed_ids", wraps=self.store.suppressed_ids) as suppressed:
            with self.assertRaisesRegex(RuntimeError, "Conversation access unavailable"):
                self.service.record(ticket, self.item)
            with self.assertRaisesRegex(RuntimeError, "Conversation access unavailable"):
                self.service.context("question")
            enqueue.assert_not_called()
            suppressed.assert_not_called()
        self.assertEqual(self.client.calls, [])

    def test_invalid_shutdown_timeout_does_not_stop_or_close_service(self):
        for timeout in (-1, True, "1", float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.service.close(timeout=timeout)
        self.assertFalse(self.service.stopped.is_set())
        self.assertFalse(self.store._closed)
        self.assertEqual(len(self.enqueue()), 2)


if __name__ == "__main__":
    unittest.main()
