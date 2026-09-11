import base64
import hashlib
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from sage_lantern.conversation_sage import (
    MAX_CONTENT_BYTES,
    PROVIDER,
    SCHEMA,
    ConversationSage,
    ConversationSageError,
    JournalEntry,
    SessionInput,
    exclude_from_general_context,
)


class FakeJournal:
    def __init__(self):
        self.entries = {}

    def reserve(self, key, fingerprint):
        if key in self.entries:
            return False
        self.entries[key] = JournalEntry(fingerprint)
        return True

    def get(self, key):
        return self.entries[key]

    def complete(self, key, receipt):
        self.entries[key] = replace(self.entries[key], receipt=receipt)


class FakeClient:
    def __init__(self):
        self.profile = dict(agent_id="agent-1", role="member", profile="companion",
                            enrollment_status="active", registration_status="active",
                            approval_required=False, clearance=1, capabilities=15,
                            can_read=True, can_write=True, home_domain="local-field-knowledge")
        self.sample = dict(owned_domains=["local-field-knowledge"],
                           readable_domains=["local-field-knowledge"],
                           writable_domains=["local-field-knowledge"], truncated=True)
        self.get_profile = Mock(side_effect=lambda: SimpleNamespace(**self.profile))
        self.domain_access_sample = Mock(side_effect=lambda: self.sample)
        self.propose = Mock(return_value=SimpleNamespace(
            memory_id="memory-1", tx_hash="tx-1", status="proposed", committed=True,
            committed_height=42, projection_confirmed=False, retryable=False))
        self.list_memories = Mock(return_value=dict(memories=[], has_more=False))
        self.embed = Mock(return_value=[0.1, 0.2])
        self.hybrid = Mock(return_value=dict(results=[]))
        self.forget = Mock(return_value=dict(status="deprecated", tx_hash="tx-forget"))
        self.get_memory = Mock(side_effect=lambda memory_id: memory(memory_id=memory_id))


def memory(**changes):
    result = dict(memory_id="memory-1", domain_tag="local-field-knowledge",
                  submitting_agent="agent-1", classification=1, provider=PROVIDER,
                  memory_type="observation", status="committed",
                  content=json.dumps(dict(schema=SCHEMA, session_id="session-1",
                                          event_id="event-1", kind="turn", text="private text"),
                                     ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    result.update(changes)
    result["content_hash"] = hashlib.sha256(result["content"].encode()).hexdigest()
    return result


class ConversationSageTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.journal = FakeJournal()
        self.tombstones = set()
        self.adapter = self.make_adapter()

    def make_adapter(self, **changes):
        arguments = dict(agent_id="agent-1", domain="local-field-knowledge",
                         classification=1, journal=self.journal,
                         is_tombstoned=lambda session_id: session_id in self.tombstones,
                         ledger_consent=True)
        arguments.update(changes)
        return ConversationSage(self.client, **arguments)

    def submit(self):
        return self.adapter.record_turn("session-1", "event-1", "private text")

    def test_explicit_classification_and_real_propose_signature(self):
        receipt = self.submit()
        arguments = self.client.propose.call_args.kwargs
        self.assertEqual(arguments["classification"], 1)
        self.assertEqual(arguments["domain_tag"], "local-field-knowledge")
        self.assertEqual(arguments["memory_type"], "observation")
        self.assertEqual(arguments["provider"], PROVIDER)
        self.assertEqual(arguments["embedding"], [0.1, 0.2])
        self.client.embed.assert_called_once_with(text=arguments["content"])
        self.assertNotIn("idempotency_key", arguments)
        self.assertNotIn("linked_memories", arguments)
        self.assertTrue(receipt.committed)
        self.assertFalse(receipt.projection_confirmed)
        self.assertEqual(receipt.status, "proposed")

    def test_restart_replay_and_conflict(self):
        receipt = self.submit()
        self.assertEqual(self.make_adapter().record_turn("session-1", "event-1", "private text"), receipt)
        self.client.propose.assert_called_once()
        with self.assertRaisesRegex(ConversationSageError, "event_content_conflict"):
            self.adapter.record_turn("session-1", "event-1", "changed")

    def test_session_and_reflection_identifiers_are_distinct(self):
        first = self.submit()
        second = self.adapter.record_turn("session-2", "event-1", "private text")
        reflection = self.adapter.reflect("session-1", "event-1", "private text")
        self.assertEqual(len({first.operation_key, second.operation_key, reflection.operation_key}), 3)
        envelope = json.loads(self.client.propose.call_args.kwargs["content"])
        self.assertEqual(envelope["kind"], "reflection")

    def test_ambiguous_failure_never_resubmits_or_leaks_body(self):
        self.client.propose.side_effect = TimeoutError("secret transcript")
        self.assertEqual(self.submit().outcome, "unknown")
        self.assertEqual(self.make_adapter().record_turn("session-1", "event-1", "private text").outcome, "unknown")
        self.client.propose.assert_called_once()

    def test_failed_receipt_storage_blocks_retry(self):
        self.journal.complete = Mock(side_effect=OSError("disk full"))
        with self.assertRaisesRegex(ConversationSageError, "receipt_persistence_failed"):
            self.submit()
        self.assertEqual(self.submit().outcome, "unknown")
        self.client.propose.assert_called_once()

    def test_journal_failure_prevents_send(self):
        self.journal.reserve = Mock(side_effect=OSError("disk full"))
        with self.assertRaisesRegex(ConversationSageError, "journal_unavailable"):
            self.submit()
        self.client.propose.assert_not_called()

    def test_missing_or_revoked_private_authority_fails_closed(self):
        for field, value in [("clearance", None), ("clearance", 0), ("clearance", True),
                             ("can_read", False), ("can_write", None), ("role", "admin"),
                             ("role", []), ("agent_id", "other"),
                             ("enrollment_status", "pending_review"), ("approval_required", None)]:
            with self.subTest(field=field, value=value):
                original = self.client.profile[field]
                self.client.profile[field] = value
                with self.assertRaises(ConversationSageError):
                    self.submit()
                self.client.profile[field] = original
        self.client.propose.assert_not_called()
        self.assertEqual(self.journal.entries, {})

    def test_exact_owned_read_write_grants_required(self):
        for field in ("owned_domains", "readable_domains", "writable_domains"):
            with self.subTest(field=field):
                self.client.sample[field] = ["other-domain"]
                with self.assertRaisesRegex(ConversationSageError, "private_domain_grant_unavailable"):
                    self.submit()
                self.client.sample[field] = ["local-field-knowledge"]
        self.client.propose.assert_not_called()

    def test_replay_rechecks_live_grants(self):
        self.submit()
        self.client.profile["clearance"] = 0
        with self.assertRaises(ConversationSageError):
            self.submit()
        self.client.propose.assert_called_once()

    def test_private_classification_and_ordinary_domain_required(self):
        for classification in (None, 0, True, 5, "1"):
            with self.subTest(classification=classification), self.assertRaises(ConversationSageError):
                self.make_adapter(classification=classification)
        for domain in ("general", "self", "meta", "sage-internal", "scope:test", "foreign/domain"):
            with self.subTest(domain=domain), self.assertRaises(ConversationSageError):
                self.make_adapter(domain=domain)

    def test_input_bounds_fail_before_sdk_calls(self):
        for content in ("", " ", "x" * MAX_CONTENT_BYTES, "\ud800"):
            with self.subTest(content=repr(content[:10])), self.assertRaises(ConversationSageError):
                self.adapter.record_turn("session-1", "event-1", content)
        for limit, offset in [(0, 0), (51, 0), (True, 0), (1, -1), (1, 7901)]:
            with self.subTest(limit=limit, offset=offset), self.assertRaises(ConversationSageError):
                self.adapter.history("session-1", limit=limit, offset=offset)
        self.client.get_profile.assert_not_called()

    def test_history_bounded_pagination_and_session_filter(self):
        other = memory()
        envelope = json.loads(other["content"])
        envelope["session_id"] = "other-session"
        other["content"] = json.dumps(envelope)
        other["content_hash"] = hashlib.sha256(other["content"].encode()).hexdigest()
        self.client.list_memories.return_value = dict(memories=[other, memory(status="proposed")], has_more=True)
        page = self.adapter.history("session-1", limit=2, offset=4)
        self.assertEqual(len(page.records), 1)
        self.assertEqual(page.records[0].status, "proposed")
        self.assertEqual(page.next_offset, 6)
        self.client.list_memories.assert_called_once_with(limit=2, offset=4, domain="local-field-knowledge",
                                                         provider=PROVIDER, agent="agent-1", sort="newest")

    def test_unknown_pagination_not_claimed_complete(self):
        self.client.list_memories.return_value = dict(memories=[])
        self.assertIsNone(self.adapter.history("session-1").has_more)

    def test_response_scope_and_schema_fail_closed(self):
        changes = [dict(classification=0), dict(classification=True),
                   dict(domain_tag="general"), dict(submitting_agent="other"), dict(provider="other-client"),
                   dict(status=[]), dict(content="not json"), dict(content="[]"),
                   dict(content=json.dumps(dict(schema=SCHEMA, session_id="session-1", event_id="event-1",
                                                kind=[], text="text")))]
        for change in changes:
            with self.subTest(change=change):
                self.client.list_memories.return_value = dict(memories=[memory(**change)], has_more=False)
                with self.assertRaises(ConversationSageError):
                    self.adapter.history("session-1")

    def test_recall_uses_local_scoped_hybrid_and_committed_only(self):
        self.client.hybrid.return_value = dict(results=[memory()])
        rows = self.adapter.recall("session-1", "remember", limit=3)
        self.assertEqual(len(rows), 1)
        self.client.hybrid.assert_called_once_with(query="remember", embedding=[0.1, 0.2],
                                                  domain_tag="local-field-knowledge", provider=PROVIDER,
                                                  top_k=3, status_filter="committed")
        self.client.hybrid.return_value = dict(results=[memory(status="proposed")])
        with self.assertRaisesRegex(ConversationSageError, "uncommitted_recall"):
            self.adapter.recall("session-1", "remember")

    def test_embedding_and_response_bounds(self):
        for embedding in ([], [True], [float("nan")], [float("inf")], [10**1000], [0.1] * 4097):
            with self.subTest(embedding=str(embedding)[:20]):
                self.client.embed.return_value = embedding
                with self.assertRaisesRegex(ConversationSageError, "invalid_embedding"):
                    self.adapter.recall("session-1", "query")
        self.client.hybrid.assert_not_called()
        self.client.list_memories.return_value = dict(memories=[memory(), memory()], has_more=False)
        with self.assertRaisesRegex(ConversationSageError, "invalid_response"):
            self.adapter.history("session-1", limit=1)

    def test_receipt_fields_do_not_infer_commit_from_status(self):
        self.client.propose.return_value = dict(memory_id="memory-1", tx_hash="tx-1", status="proposed")
        receipt = self.submit()
        self.assertIsNone(receipt.committed)
        self.assertIsNone(receipt.projection_confirmed)
        self.assertIsNone(receipt.retryable)

    def test_malformed_receipt_keeps_reservation(self):
        self.client.propose.return_value.committed = "true"
        self.assertEqual(self.submit().outcome, "unknown")
        self.assertEqual(self.submit().outcome, "unknown")

    def test_status_is_projection_evidence_not_absence_proof(self):
        self.client.get_memory.side_effect = RuntimeError("404")
        with self.assertRaisesRegex(ConversationSageError, "sage_operation_failed"):
            self.adapter.status("session-1", "memory-1")
        self.client.get_memory.side_effect = lambda memory_id: memory(memory_id=memory_id)
        self.assertEqual(self.adapter.status("session-1", "memory-1").status, "committed")

    def test_forget_does_not_claim_chain_erasure(self):
        self.client.list_memories.return_value = dict(memories=[memory()], has_more=False)
        result = self.adapter.forget("session-1", "memory-1")
        self.assertFalse(result.erased)
        self.assertIn("immutable", result.retention_limit)
        self.assertEqual(result.memory_status, "deprecated")
        self.assertTrue(result.committed)
        self.client.forget.assert_called_once_with(memory_id="memory-1",
                                                 reason="User requested private conversation withdrawal")

    def test_forget_unconfirmed_id_or_other_session_never_mutates(self):
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory()
        for session, identifier in [("session-1", "other"), ("other-session", "memory-1")]:
            with self.subTest(session=session), self.assertRaisesRegex(ConversationSageError, "memory_unconfirmed|unsafe_memory_response"):
                self.adapter.forget(session, identifier)
        self.client.forget.assert_not_called()

    def test_consent_required_even_with_grants(self):
        adapter = self.make_adapter(ledger_consent=False)
        with self.assertRaisesRegex(ConversationSageError, "immutable_ledger_consent_required"):
            adapter.ensure_session(SessionInput("session-1", "text"))
        self.client.propose.assert_not_called()

    def test_ensure_session_deterministic_drafts(self):
        item = self.adapter.draft_session("session-1", timestamp="2026-09-05T10:00:00Z",
                                          transcript="question", response="answer", reflection="lesson")
        self.assertEqual(json.loads(item.interaction), dict(timestamp="2026-09-05T10:00:00Z",
                                                          transcript="question", response="answer"))
        receipt = self.adapter.ensure_session(item)
        self.assertIsNotNone(receipt.reflection)
        self.assertEqual(self.client.propose.call_count, 2)
        self.assertEqual(self.adapter.ensure_session(item), receipt)
        self.assertEqual(self.client.propose.call_count, 2)

    def test_unknown_interaction_defers_reflection(self):
        self.client.propose.side_effect = TimeoutError()
        receipt = self.adapter.ensure_session(SessionInput("session-1", "text", "lesson"))
        self.assertEqual(receipt.interaction.outcome, "unknown")
        self.assertIsNone(receipt.reflection)
        self.client.propose.assert_called_once()

    def test_bounded_exact_reconciliation_never_resends(self):
        self.client.propose.side_effect = TimeoutError()
        self.submit()
        absent = self.adapter.reconcile("session-1", "event-1", "private text")
        self.assertEqual(absent.outcome, "unknown")
        self.client.list_memories.return_value = dict(memories=[memory()], has_more=False)
        found = self.adapter.reconcile("session-1", "event-1", "private text")
        self.assertEqual(found.outcome, "projection_observed")
        self.assertTrue(found.projection_confirmed)
        self.assertIsNone(found.committed)
        self.assertEqual(found.tx_hash, "")
        self.assertEqual(self.submit(), found)
        self.client.propose.assert_called_once()

    def test_reconciliation_rejects_wrong_hash_and_duplicates(self):
        self.client.propose.side_effect = TimeoutError()
        self.submit()
        for rows in ([memory(), memory(memory_id="memory-2")],
                     [memory(content=memory()["content"] + " ")]):
            self.client.list_memories.return_value = dict(memories=rows, has_more=False)
            with self.assertRaisesRegex(ConversationSageError, "reconciliation_conflict"):
                self.adapter.reconcile("session-1", "event-1", "private text")

    def test_exact_get_reconciliation_includes_tombstones_for_cleanup(self):
        self.client.propose.side_effect = TimeoutError()
        self.submit()
        self.tombstones.add("session-1")
        receipt = self.adapter.reconcile("session-1", "event-1", "private text", memory_id="memory-1")
        self.assertEqual(receipt.memory_id, "memory-1")
        self.client.list_memories.assert_not_called()
        self.assertFalse(self.adapter.forget("session-1", receipt.memory_id).erased)

    def test_tombstones_suppress_all_reads_and_new_writes(self):
        self.tombstones.add("session-1")
        self.assertEqual(self.adapter.history("session-1").records, ())
        self.assertEqual(self.adapter.recall("session-1", "query"), ())
        self.assertIsNone(self.adapter.status("session-1", "memory-1"))
        with self.assertRaisesRegex(ConversationSageError, "session_tombstoned"):
            self.submit()
        self.client.propose.assert_not_called()
        self.client.get_memory.assert_not_called()

    def test_delete_race_after_submit_retains_id_and_skips_reflection(self):
        response = self.client.propose.return_value
        def delete_during_submit(**arguments):
            self.tombstones.add("session-1")
            return response
        self.client.propose.side_effect = delete_during_submit
        receipt = self.adapter.ensure_session(SessionInput("session-1", "text", "lesson"))
        self.assertEqual(receipt.interaction.memory_id, "memory-1")
        self.assertIsNone(receipt.reflection)

    def test_cross_session_recall_and_recent_anaphora_fallback(self):
        self.client.list_memories.return_value = dict(memories=[memory()], has_more=False)
        rows = self.adapter.recall_conversations("what was that?", suppressed_session_ids=set())
        self.assertEqual(rows[0].session_id, "session-1")
        self.assertEqual(rows[0].memory_id, "memory-1")
        self.assertEqual(self.client.list_memories.call_args.kwargs["status"], "committed")
        self.assertEqual(self.adapter.recall_conversations("query", suppressed_session_ids={"session-1"}), ())

    def test_cross_session_deletion_race_filters_after_network(self):
        self.client.hybrid.return_value = dict(results=[memory()])
        def recent(**arguments):
            self.tombstones.add("session-1")
            return dict(memories=[])
        self.client.list_memories.side_effect = recent
        self.assertEqual(self.adapter.recall_conversations("query", suppressed_session_ids=set()), ())

    def test_tombstone_failure_fails_closed(self):
        adapter = self.make_adapter(is_tombstoned=lambda session: None)
        with self.assertRaisesRegex(ConversationSageError, "tombstone_state_unavailable"):
            adapter.history("session-1")

    def test_hash_mismatch_never_disclosed(self):
        row = memory()
        row["content_hash"] = "wrong"
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = row
        with self.assertRaisesRegex(ConversationSageError, "invalid_content_hash"):
            self.adapter.status("session-1", "memory-1")

    def test_forget_replay_of_deprecated_projection_does_not_rechallenge(self):
        self.client.get_memory.side_effect = lambda memory_id: memory(memory_id=memory_id, status="deprecated")
        result = self.adapter.forget("session-1", "memory-1")
        self.assertEqual(result.memory_status, "deprecated")
        self.assertIsNone(result.committed)
        self.client.forget.assert_not_called()

    def test_forget_challenged_or_unavailable_status_does_not_claim_deprecated(self):
        for status in ("challenged", ""):
            self.client.forget.return_value = dict(status=status, tx_hash="tx-forget")
            result = self.adapter.forget("session-1", "memory-1")
            self.assertEqual(result.memory_status, status or None)
            self.assertTrue(result.committed)
            self.assertFalse(result.erased)

    def test_forget_session_withdraws_both_known_records(self):
        first = self.client.propose.return_value
        second = SimpleNamespace(**vars(first))
        second.memory_id = "memory-2"
        self.client.propose.side_effect = [first, second]
        receipt = self.adapter.ensure_session(SessionInput("session-1", "text", "lesson"))
        self.tombstones.add("session-1")
        results = self.adapter.forget_session("session-1", receipt)
        self.assertEqual([result.memory_id for result in results], ["memory-1", "memory-2"])
        self.assertEqual(self.client.forget.call_count, 2)

    def test_unknown_session_cannot_claim_withdrawal_complete(self):
        self.client.propose.side_effect = TimeoutError()
        receipt = self.adapter.ensure_session(SessionInput("session-1", "text"))
        with self.assertRaisesRegex(ConversationSageError, "submission_uncertain_reconcile_before_forget"):
            self.adapter.forget_session("session-1", receipt)
        self.client.forget.assert_not_called()

    def test_revoked_read_grants_block_cross_session_recall_before_embedding(self):
        self.client.profile["can_read"] = False
        with self.assertRaisesRegex(ConversationSageError, "private_access_unavailable"):
            self.adapter.recall_conversations("query", suppressed_session_ids=set())
        self.client.embed.assert_not_called()
        self.client.hybrid.assert_not_called()

    def test_sdk_models_and_signatures_offline(self):
        try:
            from sage_sdk import SageClient
            from sage_sdk.models import AgentProfile, MemoryRecord, MemorySubmitResponse
        except ImportError:
            self.skipTest("optional sage-agent-sdk is not installed")
        import inspect
        self.client.get_profile.side_effect = None
        self.client.get_profile.return_value = AgentProfile.model_validate(
            dict(self.client.profile, poe_weight=1.0, vote_count=0))
        self.client.propose.return_value = MemorySubmitResponse(
            memory_id="memory-1", tx_hash="tx-1", status="proposed", committed=True)
        self.submit()
        inspect.signature(SageClient.propose).bind(None, **self.client.propose.call_args.kwargs)
        record = MemoryRecord.model_validate(dict(memory(), confidence_score=0.8,
                                                 created_at="2026-09-05T00:00:00Z"))
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = record
        self.assertEqual(self.adapter.status("session-1", "memory-1").status, "committed")
        inspect.signature(SageClient.get_memory).bind(None, **self.client.get_memory.call_args.kwargs)

    def test_exact_status_accepts_rest_omitted_provider_without_search_fallback(self):
        self.submit()
        for omit in (False, True):
            row = memory(provider=None)
            if omit:
                row.pop("provider")
            self.client.get_memory.side_effect = None
            self.client.get_memory.return_value = row
            self.assertEqual(self.adapter.status("session-1", "memory-1").memory_id, "memory-1")
        self.client.list_memories.assert_not_called()
        self.client.hybrid.assert_not_called()

    def test_omitted_provider_still_requires_every_identity_and_schema_gate(self):
        changes = [dict(domain_tag="general"), dict(submitting_agent="other"),
                   dict(classification=0), dict(classification=None), dict(memory_type="fact"),
                   dict(content="a private observation without conversation identity"),
                   dict(content="{}"), dict(status="unknown")]
        for change in changes:
            with self.subTest(change=change):
                self.client.get_memory.side_effect = None
                self.client.get_memory.return_value = memory(provider=None, **change)
                with self.assertRaises(ConversationSageError):
                    self.adapter.status("session-1", "memory-1")
        row = memory(provider=None)
        row["content_hash"] = "wrong"
        self.client.get_memory.return_value = row
        with self.assertRaisesRegex(ConversationSageError, "invalid_content_hash"):
            self.adapter.status("session-1", "memory-1")

    def test_filtered_list_reconciliation_checks_exact_content_hash(self):
        self.client.propose.side_effect = TimeoutError()
        self.submit()
        self.client.list_memories.return_value = dict(memories=[memory()], has_more=False)
        receipt = self.adapter.reconcile("session-1", "event-1", "private text")
        self.assertEqual(receipt.outcome, "projection_observed")
        self.assertEqual(receipt.memory_id, "memory-1")
        self.assertEqual(self.client.list_memories.call_args.kwargs["provider"], PROVIDER)
        self.client.propose.assert_called_once()

    def test_omitted_provider_recall_fails_without_broadening_filters(self):
        self.client.hybrid.return_value = dict(results=[memory(provider=None)])
        self.client.list_memories.return_value = dict(memories=[memory(provider=None)], has_more=False)
        with self.assertRaisesRegex(ConversationSageError, "unsafe_memory_response"):
            self.adapter.recall_conversations("old topic", suppressed_session_ids=set())
        self.assertEqual(self.client.hybrid.call_args.kwargs["provider"], PROVIDER)
        self.assertEqual(self.client.hybrid.call_args.kwargs["domain_tag"], "local-field-knowledge")
        self.client.hybrid.assert_called_once()
        self.client.list_memories.assert_called_once()
        self.assertEqual(self.client.list_memories.call_args.kwargs["provider"], PROVIDER)

    def test_omitted_provider_never_relaxes_committed_only_recall(self):
        self.client.hybrid.return_value = dict(results=[memory(provider=None, status="proposed")])
        with self.assertRaisesRegex(ConversationSageError, "uncommitted_recall_response"):
            self.adapter.recall("session-1", "topic")

    def test_empty_filtered_recall_does_not_retry_without_provider(self):
        self.assertEqual(self.adapter.recall_conversations("topic", suppressed_session_ids=set()), ())
        self.client.hybrid.assert_called_once()
        self.client.list_memories.assert_called_once()
        self.assertEqual(self.client.hybrid.call_args.kwargs["provider"], PROVIDER)
        self.assertEqual(self.client.list_memories.call_args.kwargs["provider"], PROVIDER)

    def test_omitted_provider_withdrawal_preserves_ledger_limit(self):
        self.submit()
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        result = self.adapter.forget("session-1", "memory-1")
        self.assertEqual(result.memory_status, "deprecated")
        self.assertFalse(result.erased)
        self.client.forget.assert_called_once()

    def test_generic_context_guard_excludes_unlabeled_and_disguised_conversations(self):
        for provider in (None, "", PROVIDER, "other-client"):
            with self.subTest(provider=provider):
                self.assertTrue(exclude_from_general_context(memory(provider=provider)))
        for content in ("{\"schema\":\"sage.lantern.conversation.v1\", broken JSON",
                        '{"schema":"sage\\u002elantern\\u002econversation.v1"}',
                        "sage.lantern.conversation-context.v1", "x" * (MAX_CONTENT_BYTES + 1),
                        "\ud800", None):
            with self.subTest(content=repr(content)[:60]):
                self.assertTrue(exclude_from_general_context(dict(provider="other-client", content=content)))
        self.assertTrue(exclude_from_general_context(dict(content="unlabeled content")))
        self.assertFalse(exclude_from_general_context(dict(provider="reviewed-facts", content="A bounded observation.")))

    def test_pydantic_provider_default_none_matches_deployed_detail(self):
        try:
            from sage_sdk.models import MemoryRecord
        except ImportError:
            self.skipTest("optional sage-agent-sdk is not installed")
        row = memory()
        row.pop("provider")
        record = MemoryRecord.model_validate(dict(row, confidence_score=0.8, created_at="2026-09-05T00:00:00Z"))
        self.assertIsNone(record.provider)
        self.submit()
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = record
        self.assertEqual(self.adapter.status("session-1", "memory-1").status, "committed")
        self.assertTrue(exclude_from_general_context(record))

    def test_unjournaled_exact_detail_requires_filtered_list_evidence(self):
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        self.client.list_memories.return_value = dict(memories=[memory()], has_more=False)
        record = self.adapter.status("session-1", "memory-1", offset=50)
        self.assertEqual(record.memory_id, "memory-1")
        self.client.list_memories.assert_called_once_with(limit=50, offset=50,
                                                         domain="local-field-knowledge", provider=PROVIDER,
                                                         agent="agent-1", sort="newest")
        self.client.hybrid.assert_not_called()

    def test_unjournaled_absent_provider_fails_without_exact_evidence(self):
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        with self.assertRaisesRegex(ConversationSageError, "detail_provider_evidence_unavailable"):
            self.adapter.status("session-1", "memory-1")
        self.client.list_memories.assert_called_once()
        self.assertEqual(self.client.list_memories.call_args.kwargs["provider"], PROVIDER)

    def test_unjournaled_detail_rejects_unlabeled_or_conflicting_list_evidence(self):
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        for rows in ([memory(provider=None)], [memory(memory_id="different")],
                     [memory(content=memory()["content"] + " ")], [memory(), memory()]):
            with self.subTest(rows=rows):
                self.client.list_memories.return_value = dict(memories=rows, has_more=False)
                with self.assertRaises(ConversationSageError):
                    self.adapter.status("session-1", "memory-1")

    def test_detail_journal_mismatch_never_falls_back_to_list(self):
        receipt = self.submit()
        self.journal.entries[receipt.operation_key] = replace(
            self.journal.entries[receipt.operation_key], receipt=replace(receipt, memory_id="different"))
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        with self.assertRaisesRegex(ConversationSageError, "detail_evidence_conflict"):
            self.adapter.status("session-1", "memory-1")
        self.client.list_memories.assert_not_called()

    def test_pending_journal_is_not_provider_evidence_for_exact_reconciliation(self):
        self.client.propose.side_effect = TimeoutError()
        self.submit()
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        with self.assertRaisesRegex(ConversationSageError, "detail_provider_evidence_unavailable"):
            self.adapter.reconcile("session-1", "event-1", "private text", memory_id="memory-1")
        self.client.list_memories.return_value = dict(memories=[memory()], has_more=False)
        receipt = self.adapter.reconcile("session-1", "event-1", "private text", memory_id="memory-1")
        self.assertEqual(receipt.outcome, "projection_observed")
        self.client.propose.assert_called_once()

    def test_hybrid_actual_sdk_shape_after_adapter_reopen_uses_journal_evidence(self):
        try:
            from sage_sdk.models import MemoryQueryResponse
        except ImportError:
            self.skipTest("optional sage-agent-sdk is not installed")
        self.submit()
        row = memory()
        row.pop("provider")
        row.update(confidence_score=0.8, created_at="2026-09-05T00:00:00Z")
        response = MemoryQueryResponse.model_validate(dict(results=[row], total_count=1))
        self.assertIsNone(response.results[0].provider)
        self.client.hybrid.return_value = response
        reopened = self.make_adapter()
        recalled = reopened.recall_conversations("old topic", suppressed_session_ids=set(), recent_fallback=False)
        self.assertEqual([record.memory_id for record in recalled], ["memory-1"])
        self.client.list_memories.assert_not_called()
        self.client.get_memory.assert_not_called()
        self.assertTrue(exclude_from_general_context(response.results[0]))

    def test_hybrid_missing_provider_uses_one_shared_filtered_evidence_page(self):
        first = memory()
        envelope = json.loads(first["content"])
        envelope["event_id"] = "event-2"
        second = memory(memory_id="memory-2", content=json.dumps(envelope))
        self.client.hybrid.return_value = dict(results=[dict(first, provider=None), dict(second, provider=None)])
        self.client.list_memories.return_value = dict(memories=[first, second], has_more=False)
        recalled = self.adapter.recall_conversations("topic", suppressed_session_ids=set(), recent_fallback=False)
        self.assertEqual([record.memory_id for record in recalled], ["memory-1", "memory-2"])
        self.client.list_memories.assert_called_once_with(limit=50, offset=0,
                                                         domain="local-field-knowledge", provider=PROVIDER,
                                                         agent="agent-1", sort="newest")
        self.client.get_memory.assert_not_called()

    def test_hybrid_missing_one_evidence_row_fails_entire_batch(self):
        self.client.hybrid.return_value = dict(results=[memory(provider=None), memory(provider=None, memory_id="memory-2")])
        self.client.list_memories.return_value = dict(memories=[memory()], has_more=False)
        with self.assertRaisesRegex(ConversationSageError, "recall_provider_evidence_unavailable"):
            self.adapter.recall_conversations("topic", suppressed_session_ids=set(), recent_fallback=False)
        self.client.hybrid.assert_called_once()
        self.client.list_memories.assert_called_once()

    def test_hybrid_missing_provider_rejects_stale_or_conflicting_evidence(self):
        self.client.hybrid.return_value = dict(results=[memory(provider=None)])
        for rows in ([memory(status="deprecated")], [memory(content=memory()["content"] + " ")],
                     [memory(), memory()]):
            with self.subTest(rows=rows):
                self.client.list_memories.return_value = dict(memories=rows, has_more=False)
                with self.assertRaisesRegex(ConversationSageError, "recall_provider_evidence_unavailable"):
                    self.adapter.recall("session-1", "topic")

    def test_hybrid_evidence_does_not_bypass_live_tombstones(self):
        self.submit()
        self.client.hybrid.return_value = dict(results=[memory(provider=None)])
        self.tombstones.add("session-1")
        self.assertEqual(self.adapter.recall_conversations("topic", suppressed_session_ids=set(), recent_fallback=False), ())
        self.client.list_memories.assert_not_called()

    def test_hybrid_missing_provider_keeps_authority_schema_hash_checks(self):
        self.submit()
        for changes in (dict(submitting_agent="foreign"), dict(domain_tag="general"),
                        dict(classification=0), dict(content="{}"), dict(content_hash="wrong")):
            with self.subTest(changes=changes):
                row = memory(provider=None)
                row.update(changes)
                self.client.hybrid.return_value = dict(results=[row])
                with self.assertRaises(ConversationSageError):
                    self.adapter.recall_conversations("topic", suppressed_session_ids=set(), recent_fallback=False)
        self.client.list_memories.assert_not_called()

    def test_hybrid_journal_conflict_does_not_use_list_to_override(self):
        receipt = self.submit()
        self.journal.entries[receipt.operation_key] = replace(self.journal.entries[receipt.operation_key], fingerprint="wrong")
        self.client.hybrid.return_value = dict(results=[memory(provider=None)])
        self.client.list_memories.return_value = dict(memories=[memory()], has_more=False)
        with self.assertRaisesRegex(ConversationSageError, "detail_evidence_conflict"):
            self.adapter.recall_conversations("topic", suppressed_session_ids=set(), recent_fallback=False)
        self.client.list_memories.assert_not_called()

    def test_service_context_after_durable_store_reopen_with_rest_wire_shapes(self):
        try:
            from sage_sdk.models import MemoryRecord, MemoryQueryResponse
        except ImportError:
            self.skipTest("optional sage-agent-sdk is not installed")
        import tempfile
        from pathlib import Path
        from uuid import uuid4
        from sage_lantern.conversation_service import ConversationService
        from sage_lantern.conversation_store import ConversationStore

        records = []
        def propose(**arguments):
            identifier = str(uuid4())
            records.append(MemoryRecord.model_validate(dict(
                memory_id=identifier, content=arguments["content"],
                content_hash=hashlib.sha256(arguments["content"].encode()).hexdigest(),
                submitting_agent="agent-1", domain_tag="local-field-knowledge", classification=1,
                memory_type="observation", status="committed", confidence_score=0.8,
                created_at="2026-09-05T00:00:00Z")))
            return SimpleNamespace(memory_id=identifier, tx_hash="tx-" + identifier,
                                   status="proposed", committed=True, committed_height=10,
                                   projection_confirmed=False, retryable=False)

        self.client.propose.side_effect = propose
        self.client.get_memory.side_effect = lambda memory_id: next(row for row in records if row.memory_id == memory_id)
        self.client.hybrid.side_effect = lambda **arguments: MemoryQueryResponse(results=records, total_count=len(records))
        self.client.list_memories.side_effect = lambda **arguments: dict(
            memories=[row.model_copy(update={"provider": PROVIDER, "classification": None,
                                            "content_hash": base64.b64encode(bytes.fromhex(row.content_hash)).decode("ascii")})
                      for row in records], has_more=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "store"
            store = ConversationStore(path)
            def service():
                backend = ConversationSage(self.client, agent_id="agent-1", domain="local-field-knowledge",
                                           classification=1, journal=store, is_tombstoned=store.is_suppressed,
                                           ledger_consent=True)
                return ConversationService(store, backend)
            try:
                current = service()
                current.record(store.approve(True), dict(session_id=str(uuid4()),
                               created_at="2026-09-05T00:00:00Z", question="Where is my camera?",
                               answer="You said on the desk.", has_photo=False))
                current.tick()
                self.assertEqual(self.client.propose.call_count, 2)
                self.assertTrue(all(row.provider is None for row in records))
                store.close()
                store = ConversationStore(path)
                context = service().context("Where was that camera again?")
                envelopes = [json.loads(part) for part in context]
                entries = [entry for envelope in envelopes for entry in envelope.get("entries", [])]
                self.assertEqual(len(entries), 3)
                self.assertTrue(any(entry["text"] == "Where is my camera?" for entry in entries))
                self.assertEqual(self.client.propose.call_count, 2)
                self.assertTrue(all(envelope["schema"] == "sage.lantern.conversation-context.v1" for envelope in envelopes))
            finally:
                store.close()

    def test_recent_list_missing_provider_uses_durable_journal_evidence(self):
        self.submit()
        self.client.list_memories.return_value = dict(memories=[memory(provider=None)], has_more=False)
        recalled = self.make_adapter().recall_conversations("where was that?", suppressed_session_ids=set())
        self.assertEqual([row.memory_id for row in recalled], ["memory-1"])
        self.client.list_memories.assert_called_once_with(limit=5, offset=0, domain="local-field-knowledge",
                                                         provider=PROVIDER, agent="agent-1", sort="newest",
                                                         status="committed")

    def test_recent_list_missing_provider_uses_bounded_provider_evidence(self):
        self.client.list_memories.side_effect = [
            dict(memories=[memory(provider=None)], has_more=False),
            dict(memories=[memory()], has_more=False),
        ]
        recalled = self.adapter.recall_conversations("where was that?", suppressed_session_ids=set())
        self.assertEqual([row.memory_id for row in recalled], ["memory-1"])
        self.assertEqual(self.client.list_memories.call_count, 2)
        recent, evidence = self.client.list_memories.call_args_list
        self.assertEqual(recent.kwargs["status"], "committed")
        self.assertEqual(evidence.kwargs["limit"], 50)
        self.assertNotIn("status", evidence.kwargs)
        for call in (recent, evidence):
            self.assertEqual(call.kwargs["provider"], PROVIDER)
            self.assertEqual(call.kwargs["domain"], "local-field-knowledge")
            self.assertEqual(call.kwargs["agent"], "agent-1")

    def test_recent_list_missing_provider_without_evidence_fails_closed(self):
        self.client.list_memories.side_effect = [
            dict(memories=[memory(provider=None)], has_more=False),
            dict(memories=[], has_more=False),
        ]
        with self.assertRaisesRegex(ConversationSageError, "recall_provider_evidence_unavailable"):
            self.adapter.recall_conversations("where was that?", suppressed_session_ids=set())
        self.assertEqual(self.client.list_memories.call_count, 2)
        self.client.hybrid.assert_called_once()

    def test_recent_list_missing_provider_never_recalls_noncommitted_rows(self):
        self.submit()
        for status in ("proposed", "challenged", "deprecated"):
            with self.subTest(status=status):
                self.client.list_memories.return_value = dict(memories=[memory(provider=None, status=status)], has_more=False)
                with self.assertRaisesRegex(ConversationSageError, "uncommitted_recall_response"):
                    self.adapter.recall_conversations("where was that?", suppressed_session_ids=set())

    def test_recent_list_missing_provider_tombstone_and_identity_checks_remain(self):
        self.submit()
        self.client.list_memories.return_value = dict(memories=[memory(provider=None)], has_more=False)
        self.assertEqual(self.adapter.recall_conversations("topic", suppressed_session_ids={"session-1"}), ())
        self.tombstones.add("session-1")
        self.assertEqual(self.adapter.recall_conversations("topic", suppressed_session_ids=set()), ())
        for changes in (dict(submitting_agent="foreign"), dict(domain_tag="general"),
                        dict(classification=0), dict(content_hash="wrong"), dict(content="{}")):
            with self.subTest(changes=changes):
                record = memory(provider=None)
                record.update(changes)
                self.client.list_memories.return_value = dict(memories=[record], has_more=False)
                with self.assertRaises(ConversationSageError):
                    self.adapter.recall_conversations("topic", suppressed_session_ids=set())

    def test_recent_list_missing_classification_hydrates_exact_get(self):
        self.client.list_memories.return_value = dict(memories=[memory(classification=None)], has_more=False)
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        recalled = self.adapter.recall_conversations("camera", suppressed_session_ids=set())
        self.assertEqual([row.classification for row in recalled], [1])
        self.client.get_memory.assert_called_once_with(memory_id="memory-1")
        self.client.list_memories.assert_called_once()
        self.assertEqual(self.client.list_memories.call_args.kwargs["status"], "committed")

    def test_list_classification_is_never_defaulted_when_get_also_omits_it(self):
        self.client.list_memories.return_value = dict(memories=[memory(classification=None)], has_more=False)
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(classification=None, provider=None)
        with self.assertRaisesRegex(ConversationSageError, "unsafe_memory_response"):
            self.adapter.recall_conversations("camera", suppressed_session_ids=set())
        self.client.get_memory.assert_called_once()
        self.client.list_memories.assert_called_once()

    def test_explicit_list_classification_contradictions_never_hydrate(self):
        for classification in (0, 2, True, "1"):
            with self.subTest(classification=classification):
                self.client.list_memories.return_value = dict(memories=[memory(classification=classification)], has_more=False)
                with self.assertRaisesRegex(ConversationSageError, "unsafe_memory_response"):
                    self.adapter.recall_conversations("camera", suppressed_session_ids=set())
        self.client.get_memory.assert_not_called()

    def test_missing_list_classification_requires_explicit_provider_evidence(self):
        for provider in (None, "", "other"):
            with self.subTest(provider=provider):
                self.client.list_memories.return_value = dict(memories=[memory(classification=None, provider=provider)], has_more=False)
                with self.assertRaisesRegex(ConversationSageError, "unsafe_memory_response"):
                    self.adapter.recall_conversations("camera", suppressed_session_ids=set())
        self.client.get_memory.assert_not_called()

    def test_list_get_complementary_evidence_requires_all_fields_to_agree(self):
        self.client.list_memories.return_value = dict(memories=[memory(classification=None)], has_more=False)
        self.client.get_memory.side_effect = None
        changes = [dict(memory_id="other"), dict(content=memory()["content"] + " "),
                   dict(submitting_agent="other"), dict(domain_tag="general"),
                   dict(memory_type="fact"), dict(status="deprecated"),
                   dict(provider="other"), dict(classification=0)]
        for change in changes:
            with self.subTest(change=change):
                canonical = memory(provider=None)
                canonical.update(change)
                canonical["content_hash"] = hashlib.sha256(canonical["content"].encode()).hexdigest()
                self.client.get_memory.return_value = canonical
                with self.assertRaises(ConversationSageError):
                    self.adapter.recall_conversations("camera", suppressed_session_ids=set())

    def test_provider_evidence_page_hydrates_classification_without_recursion(self):
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        self.client.list_memories.return_value = dict(memories=[memory(classification=None)], has_more=False)
        self.assertEqual(self.adapter.status("session-1", "memory-1").classification, 1)
        self.client.list_memories.assert_called_once()
        self.assertEqual(self.client.get_memory.call_count, 2)
        self.assertEqual(self.journal.entries, {})

    def test_provider_evidence_page_hydrates_only_requested_exact_ids(self):
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        self.client.list_memories.return_value = dict(
            memories=[memory(classification=None, memory_id="unrelated"), memory(classification=None)], has_more=False)
        self.assertEqual(self.adapter.status("session-1", "memory-1").classification, 1)
        self.assertEqual(self.client.get_memory.call_count, 2)
        for call in self.client.get_memory.call_args_list:
            self.assertEqual(call.kwargs["memory_id"], "memory-1")

    def test_unknown_reconciliation_uses_complementary_list_get_evidence(self):
        self.client.propose.side_effect = TimeoutError()
        self.submit()
        self.client.list_memories.return_value = dict(memories=[memory(classification=None)], has_more=False)
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        receipt = self.adapter.reconcile("session-1", "event-1", "private text")
        self.assertEqual(receipt.outcome, "projection_observed")
        self.client.propose.assert_called_once()
        self.client.list_memories.assert_called_once()
        self.client.get_memory.assert_called_once()

    def test_hybrid_provider_evidence_with_list_missing_classification(self):
        self.client.hybrid.return_value = dict(results=[memory(provider=None)])
        self.client.list_memories.return_value = dict(memories=[memory(classification=None)], has_more=False)
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        rows = self.adapter.recall_conversations("camera", suppressed_session_ids=set(), recent_fallback=False)
        self.assertEqual([row.memory_id for row in rows], ["memory-1"])
        self.client.get_memory.assert_called_once()
        self.client.list_memories.assert_called_once()

    def test_list_hydration_deletion_race_still_suppresses_recall(self):
        self.client.list_memories.return_value = dict(memories=[memory(classification=None)], has_more=False)
        def get_and_delete(memory_id):
            self.tombstones.add("session-1")
            return memory(provider=None)
        self.client.get_memory.side_effect = get_and_delete
        self.assertEqual(self.adapter.recall_conversations("camera", suppressed_session_ids=set()), ())

    def test_native_list_base64_hash_complements_exact_get_hex_hash(self):
        row = memory(classification=None)
        expected_hash = row["content_hash"]
        row["content_hash"] = base64.b64encode(bytes.fromhex(expected_hash)).decode("ascii")
        self.client.list_memories.return_value = dict(memories=[row], has_more=False)
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        recalled = self.adapter.recall_conversations("camera", suppressed_session_ids=set())
        self.assertEqual(recalled[0].content_hash, expected_hash)
        self.assertEqual(recalled[0].classification, 1)
        self.client.get_memory.assert_called_once_with(memory_id="memory-1")
        self.assertEqual(len(row["content_hash"]), 44)

    def test_list_base64_hash_reconciliation_uses_exact_content_bytes(self):
        self.client.propose.side_effect = TimeoutError()
        self.submit()
        row = memory(classification=None)
        row["content_hash"] = base64.b64encode(bytes.fromhex(row["content_hash"])).decode("ascii")
        self.client.list_memories.return_value = dict(memories=[row], has_more=False)
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = memory(provider=None)
        receipt = self.adapter.reconcile("session-1", "event-1", "private text")
        self.assertEqual(receipt.outcome, "projection_observed")
        self.client.propose.assert_called_once()

    def test_list_digest_rejects_missing_malformed_and_wrong_base64(self):
        valid = base64.b64encode(bytes.fromhex(memory()["content_hash"])).decode("ascii")
        for digest in (None, "", valid[:-1], valid + "\n", "!" * 44,
                       base64.b64encode(b"x" * 31).decode("ascii"),
                       base64.b64encode(b"x" * 32).decode("ascii")):
            with self.subTest(digest=digest):
                row = memory(classification=None)
                row["content_hash"] = digest
                self.client.list_memories.return_value = dict(memories=[row], has_more=False)
                with self.assertRaisesRegex(ConversationSageError, "invalid_content_hash"):
                    self.adapter.history("session-1")
        self.client.get_memory.assert_not_called()

    def test_get_digest_stays_strict_hex_not_list_base64(self):
        row = memory(provider=None)
        row["content_hash"] = base64.b64encode(bytes.fromhex(row["content_hash"])).decode("ascii")
        self.client.get_memory.side_effect = None
        self.client.get_memory.return_value = row
        with self.assertRaisesRegex(ConversationSageError, "invalid_content_hash"):
            self.adapter.status("session-1", "memory-1")
        self.client.list_memories.assert_not_called()

    def test_list_base64_digest_does_not_authorize_truncated_content(self):
        row = memory(classification=None)
        row["content_hash"] = base64.b64encode(bytes.fromhex(row["content_hash"])).decode("ascii")
        row["content"] = row["content"][:20]
        self.client.list_memories.return_value = dict(memories=[row], has_more=False)
        with self.assertRaisesRegex(ConversationSageError, "invalid_content_hash"):
            self.adapter.history("session-1")
        self.client.get_memory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
