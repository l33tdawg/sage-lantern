"""Guard/CAS races and legacy interface checks with in-memory protected storage."""

import copy
from dataclasses import FrozenInstanceError, asdict, replace
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_conversation_store as legacy
from test_workflow_store import setup as signed_workflow
from sage_lantern import conversation_records as records
from sage_lantern.conversation_store import ConversationStoreError
from sage_lantern.conversation_workflow_store import ConversationWorkflowStore
from sage_lantern.message_storage import MessageStorageSnapshot
from sage_lantern.workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore, WorkflowUnavailable


class ProtectedWorkflows(WorkflowStore):
    def __init__(self):
        self.storage = SimpleNamespace(actor=SimpleNamespace(chain_id="private", agent_id="a" * 64))
        self.storage_epoch = MessageStorageSnapshot("e" * 64, "1")
        self.storage.snapshot = lambda: self.storage_epoch
        self.rows = {}
        self.calls = []
        self.mutex = threading.Lock()
        self.before_put = None
        self.lose_ack = False
        self.extra_count = 0

    def get(self, record_id, *, kind, access_check):
        if access_check() is not True:
            raise WorkflowUnavailable()
        with self.mutex:
            if record_id not in self.rows:
                raise WorkflowNotFound()
            row = copy.deepcopy(self.rows[record_id])
        if row["kind"] != kind:
            raise WorkflowUnavailable()
        return row

    def put(self, record_id, kind, expected_revision, payload, *, access_check, guard=None):
        if self.before_put is not None:
            callback, self.before_put = self.before_put, None
            callback()
        if access_check() is not True:
            raise WorkflowUnavailable()
        with self.mutex:
            self.calls.append(dict(record_id=record_id, kind=kind, expected_revision=expected_revision, guard=guard))
            if self.rows.get(record_id, {}).get("revision", 0) != expected_revision:
                raise WorkflowOutcomeUnknown()
            if guard is not None and self.rows.get(guard["record_id"], {}).get("revision") != guard["expected_revision"]:
                raise WorkflowOutcomeUnknown()
            row = dict(record_id=record_id, kind=kind, revision=expected_revision + 1, payload=copy.deepcopy(payload))
            self.rows[record_id] = row
            if self.lose_ack:
                raise WorkflowOutcomeUnknown()
            return copy.deepcopy(row)

    def scan(self, *, limit, max_records, access_check):
        if access_check() is not True or len(self.rows) + self.extra_count > max_records:
            raise WorkflowUnavailable()
        with self.mutex:
            return copy.deepcopy([self.rows[key] for key in sorted(self.rows)]) + [dict(kind="mesh_outbound")] * self.extra_count


class ConversationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = ProtectedWorkflows()
        self.allowed = True
        self.store = ConversationWorkflowStore(self.workflow, access_check=lambda: self.allowed)
        self.item = dict(session_id=str(uuid4()), created_at="2026-09-05T10:00:00Z", question="Where is my camera?",
                         answer="On your desk.", has_photo=True, reflection="The user asked about a camera.")
        self.key = "a" * 64
        self.fingerprint = "b" * 64
        self.link = dict(object_id=str(uuid4()), digest="f" * 64, backend="sage-vault-blob")

    enqueue = legacy.ConversationStoreTests.enqueue
    receipt = legacy.ConversationStoreTests.receipt
    reserve = legacy.ConversationStoreTests.reserve

    def test_exact_suppression_reads_never_scan_and_remain_live(self):
        self.enqueue()
        session_id = self.item["session_id"]
        absent = str(uuid4())
        with patch.object(self.workflow, "scan", side_effect=AssertionError("unexpected inventory")):
            self.assertFalse(self.store.is_suppressed(session_id))
            self.assertFalse(self.store.is_suppressed(absent))
            self.store.suppress([absent])
            self.assertTrue(self.store.is_suppressed(absent))
            self.store.clear()
            self.assertTrue(self.store.is_suppressed(session_id))
            self.allowed = False
            with self.assertRaises(ConversationStoreError):
                self.store.is_suppressed(session_id)

    def test_exact_suppression_rejects_control_change_during_read(self):
        self.enqueue()
        original = self.store._session

        def changed(session_id):
            row = original(session_id)
            self.store.clear()
            return row

        with patch.object(self.store, "_session", side_effect=changed):
            with self.assertRaises(ConversationStoreError):
                self.store.is_suppressed(self.item["session_id"])

    def test_exact_suppression_rechecks_access_after_read(self):
        self.enqueue()
        original = self.store._session

        def revoked(session_id):
            row = original(session_id)
            self.allowed = False
            return row

        with patch.object(self.store, "_session", side_effect=revoked):
            with self.assertRaises(ConversationStoreError):
                self.store.is_suppressed(self.item["session_id"])

    def reopen(self, **options):
        self.store.close()
        self.store = ConversationWorkflowStore(self.workflow, access_check=lambda: self.allowed, **options)

    test_duplicate_exact_payload_conflict_and_no_mutation = legacy.ConversationStoreTests.test_duplicate_exact_payload_conflict_and_no_mutation
    test_optout_invalidates_both_generation_scopes = legacy.ConversationStoreTests.test_optout_invalidates_both_generation_scopes
    test_external_generation_invalidates_capture_not_durable_work = legacy.ConversationStoreTests.test_external_generation_invalidates_capture_not_durable_work
    test_delete_rotation_preserves_unrelated_queue_and_payload = legacy.ConversationStoreTests.test_delete_rotation_preserves_unrelated_queue_and_payload
    test_optout_reenable_does_not_revive_unknown_proven_unsent = legacy.ConversationStoreTests.test_optout_reenable_does_not_revive_unknown_proven_unsent
    test_history_refresh_does_not_revoke_unknown_proven_unsent = legacy.ConversationStoreTests.test_history_refresh_does_not_revoke_unknown_proven_unsent
    test_reopen_dispatching_is_unknown_not_queued = legacy.ConversationStoreTests.test_reopen_dispatching_is_unknown_not_queued
    test_suppression_delete_race_and_late_receipt = legacy.ConversationStoreTests.test_suppression_delete_race_and_late_receipt
    test_unknown_suppressed_proven_unsent_stays_cancelled = legacy.ConversationStoreTests.test_unknown_suppressed_proven_unsent_stays_cancelled
    test_tombstone_before_enqueue_and_idempotent_suppress = legacy.ConversationStoreTests.test_tombstone_before_enqueue_and_idempotent_suppress
    test_journal_reserved_before_send_never_replays_after_reopen = legacy.ConversationStoreTests.test_journal_reserved_before_send_never_replays_after_reopen
    test_journal_binding_conflict_and_optout_gate = legacy.ConversationStoreTests.test_journal_binding_conflict_and_optout_gate
    test_journal_complete_poll_and_suppression_preserve_provenance = legacy.ConversationStoreTests.test_journal_complete_poll_and_suppression_preserve_provenance
    test_late_journal_receipt_after_delete_stays_suppressed = legacy.ConversationStoreTests.test_late_journal_receipt_after_delete_stays_suppressed
    test_adapter_actual_reserve_complete_and_unknown_replay = legacy.ConversationStoreTests.test_adapter_actual_reserve_complete_and_unknown_replay

    def test_initial_off_reopen_does_not_reset_consent_and_source_stored_once(self):
        self.assertIsNone(self.store.consent())
        self.reopen(approved=True)
        self.assertIsNone(self.store.consent())
        operations = self.enqueue()
        self.assertEqual(len(self.workflow.rows), 2)
        row = next(row for row in self.workflow.rows.values() if row["kind"] == "conversation_session")
        encoded = json.dumps(row["payload"])
        self.assertEqual(encoded.count(self.item["question"]), 1)
        self.assertEqual(encoded.count(self.item["answer"]), 1)
        before = self.store.pending()
        self.reopen()
        self.assertEqual(self.store.pending(), before)
        self.assertEqual(len(operations), 2)

    def test_guarded_enqueue_cannot_cross_consent_revocation(self):
        ticket = self.store.approve(True)
        self.workflow.before_put = lambda: self.store.approve(False)
        with self.assertRaises(ConversationStoreError):
            self.store.enqueue(ticket, self.item)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(len(self.workflow.rows), 1)

    def test_guarded_reserve_cannot_cross_suppression(self):
        operation = self.enqueue()[0]
        self.workflow.before_put = lambda: self.store.suppress([self.item["session_id"]])
        with self.assertRaises(ConversationStoreError):
            self.reserve(operation)
        self.assertEqual(self.store.operation(operation)["state"], "cancelled")
        with self.assertRaises(ConversationStoreError):
            self.store.get(self.key)

    def test_all_session_mutations_include_exact_control_guard(self):
        operation = self.enqueue()[0]
        self.reserve(operation)
        self.store.complete(self.key, self.receipt())
        calls = [call for call in self.workflow.calls if call["kind"] == "conversation_session"]
        self.assertEqual(len(calls), 3)
        for call in calls:
            self.assertEqual(call["guard"]["record_id"], self.store.control_id)
            self.assertGreater(call["guard"]["expected_revision"], 0)

    def test_unknown_reserve_never_requeues_and_late_receipt_survives(self):
        operation = self.enqueue()[0]
        self.workflow.lose_ack = True
        with self.assertRaisesRegex(ConversationStoreError, "outcome_unknown"):
            self.reserve(operation)
        self.workflow.lose_ack = False
        self.reopen()
        self.assertFalse(self.reserve(operation))
        self.assertEqual(self.store.operation(operation)["state"], "unknown")
        self.store.suppress([self.item["session_id"]])
        self.store.complete(self.key, self.receipt())
        self.assertTrue(self.store.operation(operation)["suppressed"])
        self.assertEqual(self.store.get(self.key).receipt.memory_id, "memory-1")

    def test_concurrent_reservation_has_one_admission_and_no_unknown_retry(self):
        operation = self.enqueue()[0]
        barrier = threading.Barrier(2)
        original_put = self.workflow.put
        def raced(*args, **kwargs):
            if args[1] == "conversation_session":
                barrier.wait(timeout=2)
            return original_put(*args, **kwargs)
        self.workflow.put = raced
        outcomes = []
        def reserve():
            try:
                outcomes.append(self.reserve(operation))
            except ConversationStoreError:
                outcomes.append("unknown")
        workers = [threading.Thread(target=reserve) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertCountEqual(outcomes, [True, "unknown"])

    def test_suppress_many_is_single_cas_and_oversize_has_no_partial_effect(self):
        self.enqueue()
        before = len(self.workflow.calls)
        identifiers = [str(uuid4()) for _ in range(20)] + [self.item["session_id"]]
        self.store.suppress(identifiers)
        self.assertEqual(len(self.workflow.calls), before + 1)
        previous = self.store.suppressed_ids()
        with self.assertRaises(ConversationStoreError):
            self.store.suppress([str(uuid4()) for _ in range(4096)])
        self.assertEqual(self.store.suppressed_ids(), previous)
        self.assertEqual(len(self.workflow.calls), before + 1)

    def test_clear_epoch_atomically_suppresses_all_old_sessions_and_invalidates_capture(self):
        old = self.store.approve(True)
        self.store.enqueue(old, self.item)
        before = len(self.workflow.calls)
        self.assertTrue(self.store.clear())
        self.assertEqual(len(self.workflow.calls), before + 1)
        self.assertEqual(self.store.list(), [])
        self.assertIn(self.item["session_id"], self.store.suppressed_ids())
        self.assertEqual(self.store.enqueue(old, {**self.item, "session_id": str(uuid4())}), [])
        self.assertEqual(self.store.enqueue(self.store.consent(), self.item), [])
        self.assertEqual(len(self.store.enqueue(self.store.consent(), {**self.item, "session_id": str(uuid4())})), 2)

    def test_media_intent_before_text_and_link_only_after_current_consent(self):
        ticket = self.store.approve(True)
        intent = self.store.prepare_media(ticket, self.item["session_id"], self.link)
        self.assertEqual(intent["state"], "pending")
        self.reopen()
        self.assertEqual(self.store.media(self.item["session_id"]), intent)
        self.assertEqual(self.store.list(), [])
        self.store.enqueue(ticket, self.item)
        self.assertEqual(self.store.link_media(ticket, self.item["session_id"], self.link), self.link)
        self.assertEqual(self.store.media(self.item["session_id"])["state"], "linked")
        self.store.suppress([self.item["session_id"]])
        self.assertIsNone(self.store.media(self.item["session_id"]))
        self.assertEqual(self.store.media(self.item["session_id"], include_suppressed=True)["link"], self.link)

    def test_media_guard_rechecks_suppression_after_blob_put(self):
        ticket = self.store.approve(True)
        self.store.prepare_media(ticket, self.item["session_id"], self.link)
        self.workflow.before_put = lambda: self.store.suppress([self.item["session_id"]])
        with self.assertRaises(ConversationStoreError):
            self.store.link_media(ticket, self.item["session_id"], self.link)
        self.assertEqual(self.store.media(self.item["session_id"], include_suppressed=True)["state"], "pending")

    def test_media_unknown_intent_cannot_authorize_blob_write(self):
        ticket = self.store.approve(True)
        self.workflow.lose_ack = True
        with self.assertRaisesRegex(ConversationStoreError, "outcome_unknown"):
            self.store.prepare_media(ticket, self.item["session_id"], self.link)
        self.workflow.lose_ack = False
        self.reopen()
        intent = self.store.media(self.item["session_id"])
        self.assertEqual(intent["state"], "pending")
        with self.assertRaises(ConversationStoreError):
            self.store.prepare_media(ticket, self.item["session_id"], {**self.link, "digest": "e" * 64})

    def test_caller_exact_media_api_atomically_reserves_text_then_commits_photo_reference(self):
        ticket = self.store.approve(True, history_generation="history-1")
        before = len(self.workflow.calls)
        intent = self.store.reserve_media(self.item["session_id"], self.link["digest"], ticket=ticket,
                                         item=self.item, access_check=lambda: True)
        self.assertIsInstance(intent, records.MediaIntent)
        self.assertEqual(len(self.workflow.calls), before + 1)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.pending(), [])
        with self.assertRaises(ConversationStoreError):
            self.reserve(records.operation_id(self.item["session_id"], "interaction"))
        self.reopen()
        self.assertTrue(self.store.commit_media(intent, access_check=lambda: True))
        self.assertTrue(self.store.commit_media(intent, access_check=lambda: True))
        self.assertNotIn("photo_ref", self.store.list()[0])
        self.assertEqual(self.store.list()[0]["photo_reference"], dict(object_id=self.item["session_id"],
                         digest=self.link["digest"], backend="sage.private-media.v1"))
        self.assertEqual(len(self.store.pending()), 2)
        self.assertEqual(len(self.store.enqueue(ticket, self.item)), 2)

    def test_caller_media_api_rejects_stale_consent_wrong_intent_and_per_call_lock(self):
        ticket = self.store.approve(True)
        intent = self.store.reserve_media(self.item["session_id"], self.link["digest"], ticket=ticket,
                                         item=self.item, access_check=lambda: True)
        for change in ({"digest": "e" * 64}, {"intent_id": str(uuid4())}, {"actor_id": "e" * 64}):
            with self.subTest(change=change), self.assertRaises(ConversationStoreError):
                self.store.commit_media(replace(intent, **change), access_check=lambda: True)
        with self.assertRaises(ConversationStoreError):
            self.store.commit_media(intent, access_check=lambda: False)
        self.store.approve(False)
        self.store.approve(True)
        with self.assertRaises(ConversationStoreError):
            self.store.commit_media(intent, access_check=lambda: True)
        self.assertEqual(self.store.list(), [])

    def test_caller_media_commit_guard_prevents_delete_race(self):
        ticket = self.store.approve(True)
        intent = self.store.reserve_media(self.item["session_id"], self.link["digest"], ticket=ticket,
                                         item=self.item, access_check=lambda: True)
        self.workflow.before_put = lambda: self.store.clear()
        with self.assertRaises(ConversationStoreError):
            self.store.commit_media(intent, access_check=lambda: True)
        self.assertEqual(self.store.media(self.item["session_id"], include_suppressed=True)["state"], "pending")

    def test_pending_count_needs_separate_live_drain_lease(self):
        self.enqueue()
        with self.assertRaises(ConversationStoreError):
            self.store.pending_count()
        lifecycle = [True]
        store = ConversationWorkflowStore(self.workflow, access_check=lambda: self.allowed, drain_check=lambda: lifecycle[0])
        self.allowed = False
        self.assertEqual(store.pending_count(), 2)
        with self.assertRaises(ConversationStoreError):
            store.list()
        lifecycle[0] = False
        with self.assertRaises(ConversationStoreError):
            store.pending_count()

    def drain_store(self):
        return ConversationWorkflowStore(self.workflow, access_check=lambda: self.allowed, drain_check=lambda: True)

    def test_drain_snapshot_typed_queued_unknown_dispatching_and_pending_media(self):
        self.store = self.drain_store()
        operations = self.enqueue()
        snapshot = self.store.drain_snapshot()
        self.assertEqual(asdict(snapshot), dict(storage_epoch="e" * 64 + ":1", queued=2, dispatching=0, unknown=0, pending_media=0))
        with self.assertRaises(FrozenInstanceError):
            snapshot.queued = 0
        self.store.mark_dispatching(operations[0])
        self.assertEqual(self.store.drain_snapshot().dispatching, 1)
        self.store.mark_unknown(operations[0])
        self.assertEqual(self.store.drain_snapshot().unknown, 1)
        item = {**self.item, "session_id": str(uuid4())}
        self.store.reserve_media(item["session_id"], "f" * 64, ticket=self.store.consent(), item=item, access_check=lambda: True)
        snapshot = self.store.drain_snapshot()
        self.assertEqual((snapshot.queued, snapshot.unknown, snapshot.pending_media), (3, 1, 1))
        self.assertNotIn(self.item["question"], repr(snapshot))
        self.allowed = False
        self.assertEqual(self.store.drain_snapshot(), snapshot)

    def test_drain_snapshot_requires_genuine_callback_and_live_probe(self):
        with self.assertRaises(ConversationStoreError):
            self.store.drain_snapshot()
        for guard in (lambda: False, lambda: 1):
            store = ConversationWorkflowStore(self.workflow, access_check=lambda: True, drain_check=guard)
            with self.assertRaises(ConversationStoreError):
                store.drain_snapshot()
        store = self.drain_store()
        def unavailable():
            raise WorkflowUnavailable()
        self.workflow.storage.snapshot = unavailable
        with self.assertRaises(ConversationStoreError):
            store.drain_snapshot()

    def test_drain_snapshot_rejects_epoch_change_across_whole_scan(self):
        self.enqueue()
        store = self.drain_store()
        original_scan = self.workflow.scan
        def changed(**arguments):
            result = original_scan(**arguments)
            self.workflow.storage_epoch = MessageStorageSnapshot("e" * 64, "2")
            return result
        self.workflow.scan = changed
        with self.assertRaisesRegex(ConversationStoreError, "storage_epoch_changed"):
            store.drain_snapshot()

    def test_drain_snapshot_rejects_incomplete_corrupt_duplicate_and_control_changes(self):
        self.enqueue()
        store = self.drain_store()
        original_scan = self.workflow.scan
        for mode in ("missing_control", "duplicate", "corrupt", "incomplete", "control_change"):
            def altered(**arguments):
                result = original_scan(**arguments)
                if mode == "incomplete":
                    raise WorkflowUnavailable()
                if mode == "missing_control":
                    return [row for row in result if row["kind"] != "conversation_control"]
                if mode == "duplicate":
                    return result + [result[0]]
                if mode == "corrupt":
                    next(row for row in result if row["kind"] == "conversation_session")["payload"]["item_hash"] = "f" * 64
                if mode == "control_change":
                    self.workflow.rows[store.control_id]["revision"] += 1
                return result
            self.workflow.scan = altered
            with self.subTest(mode=mode), self.assertRaises(ConversationStoreError):
                store.drain_snapshot()

    def test_drain_snapshot_rechecks_lease_after_last_probe(self):
        lease = [True]
        store = ConversationWorkflowStore(self.workflow, access_check=lambda: True, drain_check=lambda: lease[0])
        calls = []
        def snapshot():
            calls.append(True)
            if len(calls) == 2:
                lease[0] = False
            return self.workflow.storage_epoch
        self.workflow.storage.snapshot = snapshot
        with self.assertRaises(ConversationStoreError):
            store.drain_snapshot()

    def test_quota_shared_with_other_workflows_and_ascii_expansion_bounded(self):
        ticket = self.store.approve(True)
        self.workflow.extra_count = 4095
        with self.assertRaises(ConversationStoreError):
            self.store.enqueue(ticket, self.item)
        self.workflow.extra_count = 0
        with self.assertRaises(ConversationStoreError):
            self.store.enqueue(ticket, {**self.item, "answer": "é" * 3000})
        self.assertEqual(len(self.workflow.rows), 1)

    def test_closed_locked_and_corrupt_records_fail_closed(self):
        operation = self.enqueue()[0]
        self.allowed = False
        with self.assertRaises(ConversationStoreError):
            self.store.operation(operation)
        self.allowed = True
        row = next(row for row in self.workflow.rows.values() if row["kind"] == "conversation_session")
        row["payload"]["item"]["answer"] = "tampered"
        with self.assertRaises(ConversationStoreError):
            self.store.list()
        self.store.close()
        with self.assertRaises(ConversationStoreError):
            self.store.consent()


def test_real_sdk_guarded_conversation_and_media_round_trip(signed_workflow):
    fixture = signed_workflow
    calls = []
    original_put = fixture.client.workflow_put
    def capture(**arguments):
        calls.append(arguments)
        return original_put(**arguments)
    fixture.client.workflow_put = capture
    store = ConversationWorkflowStore(fixture.store, access_check=lambda: True, drain_check=lambda: True)
    ticket = store.approve(True)
    session_id = str(uuid4())
    item = dict(session_id=session_id, created_at="2026-09-06T10:00:00Z", question="Question",
                answer="Answer", has_photo=True, reflection="Unverified reflection")
    intent = store.reserve_media(session_id, "e" * 64, ticket=ticket, item=item, access_check=lambda: True)
    assert store.list() == []
    assert store.commit_media(intent, access_check=lambda: True) is True
    reopened = ConversationWorkflowStore(fixture.store, access_check=lambda: True)
    assert "photo_ref" not in reopened.list()[0]
    assert reopened.list()[0]["photo_reference"] == dict(object_id=session_id, digest="e" * 64, backend="sage.private-media.v1")
    assert len(reopened.pending()) == 2
    snapshot = store.drain_snapshot()
    assert snapshot.storage_epoch == fixture.probe.snapshot().epoch
    assert (snapshot.queued, snapshot.dispatching, snapshot.unknown, snapshot.pending_media) == (2, 0, 0, 0)
    session_calls = [call for call in calls if call["kind"] == "conversation_session"]
    assert len(session_calls) == 2
    assert all(call["guard"] == dict(record_id=store.control_id, expected_revision=2) for call in session_calls)
    assert all("propose" not in call[1] and "memories" not in call[1] for call in fixture.calls)
