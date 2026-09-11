"""Synthetic protected storage with CAS; no services, keys or disk journals."""

import copy
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
import uuid

import pytest
from sage_sdk import AgentIdentity
from test_workflow_store import setup as signed_workflow

from sage_lantern.community_public import CommunityPublic, PublicReceipt
from sage_lantern.public_workflow_store import PublicWorkflowStore
from sage_lantern.workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore, WorkflowUnavailable


OPERATION = "00000000-0000-4000-8000-000000000001"
MEMORY = "00000000-0000-4000-8000-000000000002"
DATE = "2026-09-06T10:00:00Z"


class ProtectedDouble(WorkflowStore):
    def __init__(self):
        self.identity = AgentIdentity.from_seed(b"s" * 32)
        self.storage = SimpleNamespace(actor=SimpleNamespace(chain_id="private", agent_id=self.identity.agent_id))
        self.rows = {}
        self.calls = []
        self.available = True
        self.lose_ack = False
        self.mutex = threading.Lock()

    def check(self, guard):
        if not self.available or guard() is not True:
            raise WorkflowUnavailable()

    def get(self, record_id, *, kind, access_check):
        self.check(access_check)
        self.calls.append(("get", record_id))
        if record_id not in self.rows:
            raise WorkflowNotFound()
        return copy.deepcopy(self.rows[record_id])

    def put(self, record_id, kind, expected_revision, payload, *, access_check):
        self.check(access_check)
        with self.mutex:
            self.calls.append(("put", record_id))
            current = self.rows.get(record_id, {}).get("revision", 0)
            if current != expected_revision:
                raise WorkflowOutcomeUnknown()
            row = dict(record_id=record_id, revision=current + 1, kind=kind, payload=copy.deepcopy(payload))
            self.rows[record_id] = row
            if self.lose_ack:
                raise WorkflowOutcomeUnknown()
            return copy.deepcopy(row)

    def list(self, *, limit, access_check):
        self.check(access_check)
        return dict(items=[])

    def scan(self, *, limit, max_records, access_check):
        self.check(access_check)
        self.calls.append(("scan", max_records))
        if len(self.rows) > max_records:
            raise WorkflowUnavailable()
        return copy.deepcopy(list(self.rows.values()))


class PublicWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = ProtectedDouble()
        self.identity = AgentIdentity.from_seed(b"s" * 32)
        self.workflow.storage.actor.agent_id = self.identity.agent_id
        self.allowed = True
        self.drain = True
        self.store = self.open()
        self.public = CommunityPublic(None, self.store.sdk_journal, chain_id="public", domain="commons", author="b" * 64)
        self.proposal = self.public.prepare(operation_id=OPERATION, question="Why blue?", answer="Scattered light.")
        self.receipt = PublicReceipt(OPERATION, self.proposal.content_hash, "public", MEMORY, "c" * 64, 4)

    def open(self, **options):
        return PublicWorkflowStore(self.workflow, chain_id="public", domain="commons", author="b" * 64,
                                   access_check=lambda: self.allowed, drain_check=lambda: self.drain,
                                   identity=self.identity, **options)

    def prepare(self):
        self.assertTrue(self.store.prepare_public(self.proposal, DATE))

    def test_private_source_binding_reopens_without_public_leak(self):
        source = str(uuid.uuid4())
        other = str(uuid.uuid4())
        self.store.prepare_public_source(self.proposal, DATE, source)
        self.assertNotIn(source, self.proposal.content)
        self.assertIsNone(self.open().public_sources([source])[source]["receipt"])
        self.store.sdk_journal.reserve(OPERATION, self.proposal.content_hash)
        self.store.sdk_journal.complete(OPERATION, self.receipt)
        result = self.open().public_sources([source, other])
        self.assertEqual(result[source]["receipt"], self.receipt)
        self.assertIsNone(result[other])
        with self.assertRaises(WorkflowUnavailable):
            self.store.prepare_public_source(self.proposal, DATE, other)

    def test_unsigned_cancellation_cannot_hide_signed_source(self):
        source = str(uuid.uuid4())
        self.store.prepare_public_source(self.proposal, DATE, source)
        self.store.sdk_journal.reserve(OPERATION, self.proposal.content_hash)
        self.store.sdk_journal.complete(OPERATION, self.receipt)
        payload = self.workflow.rows[self.store._id("sdk", OPERATION)]["payload"]
        payload["result"] = {"cancelled": True}
        result = self.open().public_sources([source])[source]
        self.assertIsNotNone(result)
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["proposal"], self.proposal)

    def test_source_lookup_unknown_legacy_or_storage_never_empty(self):
        self.prepare()
        with self.assertRaises(WorkflowUnavailable):
            self.store.public_sources([str(uuid.uuid4())])
        self.workflow.available = False
        with self.assertRaises(WorkflowUnavailable):
            self.store.public_sources([])

    def test_source_reassignment_and_proof_swap_rejected(self):
        source, other = str(uuid.uuid4()), str(uuid.uuid4())
        proposal = self.public.prepare(operation_id=str(uuid.uuid4()), question="Other?", answer="Different.")
        self.store.prepare_public_source(self.proposal, DATE, source)
        self.store.prepare_public_source(proposal, DATE, other)
        first = self.workflow.rows[self.store._id("sdk", OPERATION)]["payload"]
        second = self.workflow.rows[self.store._id("sdk", proposal.operation_id)]["payload"]
        original = copy.deepcopy(first)
        for changes in ({"source_id": other}, {"source_proof": second["source_proof"]},
                        {"source_id": other, "source_proof": second["source_proof"]},
                        {"created_at": "2026-09-07T10:00:00Z"}):
            with self.subTest(changes=changes):
                first.clear()
                first.update({**original, **changes})
                with self.assertRaises(WorkflowUnavailable):
                    self.open().public_sources([source, other])
        first.clear()
        first.update(original)
        self.assertIsNotNone(self.open().public_sources([source])[source])

    def test_existing_unsigned_source_never_backfilled(self):
        source = str(uuid.uuid4())
        self.store.prepare_public_source(self.proposal, DATE, source)
        row = self.workflow.rows[self.store._id("sdk", OPERATION)]
        del row["payload"]["source_proof"]
        before = copy.deepcopy(self.workflow.rows)
        with self.assertRaises(WorkflowUnavailable):
            self.store.prepare_public_source(self.proposal, DATE, source)
        with self.assertRaises(WorkflowUnavailable):
            self.store.public_sources([source])
        self.assertEqual(self.workflow.rows, before)

    def test_source_proof_cannot_move_to_other_actor_or_chain(self):
        source = str(uuid.uuid4())
        self.store.prepare_public_source(self.proposal, DATE, source)
        original = copy.deepcopy(self.workflow.rows[self.store._id("sdk", OPERATION)])
        for change in ("actor", "storage_chain", "public_chain"):
            with self.subTest(change=change):
                workflow = ProtectedDouble()
                identity = workflow.identity
                public_chain = "public"
                if change == "actor":
                    identity = AgentIdentity.from_seed(b"z" * 32)
                    workflow.storage.actor.agent_id = identity.agent_id
                elif change == "storage_chain":
                    workflow.storage.actor.chain_id = "other-private"
                else:
                    public_chain = "other-public"
                store = PublicWorkflowStore(workflow, chain_id=public_chain, domain="commons", author="b" * 64,
                    access_check=lambda: True, drain_check=lambda: True, identity=identity)
                row = copy.deepcopy(original)
                row["payload"]["binding"] = dict(store._binding)
                row["record_id"] = store._id("sdk", OPERATION)
                workflow.rows[row["record_id"]] = row
                with self.assertRaises(WorkflowUnavailable):
                    store.public_sources([source])

    def test_missing_or_wrong_identity_denies_source_proof(self):
        unconfigured = PublicWorkflowStore(self.workflow, chain_id="public", domain="commons", author="b" * 64,
            access_check=lambda: self.allowed, drain_check=lambda: self.drain)
        with self.assertRaises(WorkflowUnavailable):
            unconfigured.prepare_public_source(self.proposal, DATE, str(uuid.uuid4()))
        with self.assertRaises(WorkflowUnavailable):
            unconfigured.public_sources([])
        with self.assertRaises(WorkflowUnavailable):
            PublicWorkflowStore(self.workflow, chain_id="public", domain="commons", author="b" * 64,
                access_check=lambda: self.allowed, drain_check=lambda: self.drain,
                identity=AgentIdentity.from_seed(b"w" * 32))
        self.assertEqual(self.workflow.rows, {})

    def test_separate_namespaces_reopen_and_history_before_http_completion(self):
        self.assertTrue(self.store.reserve(OPERATION, "d" * 64))
        self.prepare()
        self.assertTrue(self.store.sdk_journal.reserve(OPERATION, self.proposal.content_hash))
        self.assertEqual(len(self.workflow.rows), 2)
        self.assertTrue(self.store.sdk_journal.complete(OPERATION, self.receipt))
        reopened = self.open()
        self.assertEqual(reopened.list_public(), [dict(proposal=self.proposal, receipt=self.receipt, created_at=DATE)])
        self.assertEqual(reopened.pending_count(), 1)
        self.assertTrue(reopened.record_public(self.proposal, self.receipt, DATE))
        self.assertTrue(reopened.complete(OPERATION, {"id": MEMORY, "state": "pending", "created_at": DATE}))
        self.assertEqual(reopened.pending_count(), 0)
        self.assertNotIn("accepted", str(reopened.list_public()))

    def test_preparation_is_not_submission_admission_and_never_recycled(self):
        with self.assertRaises(WorkflowNotFound):
            self.store.sdk_journal.reserve(OPERATION, self.proposal.content_hash)
        self.prepare()
        self.assertEqual(self.store.pending_count(), 1)
        self.assertEqual(self.store.list_public(), [])
        state = self.store.reconcile_public(OPERATION)
        self.assertFalse(state["reserved"])
        self.assertIsNone(state["receipt"])
        self.assertTrue(self.store.sdk_journal.reserve(OPERATION, self.proposal.content_hash))
        self.assertFalse(self.open().sdk_journal.reserve(OPERATION, self.proposal.content_hash))

    def test_unreserved_preparation_cancel_is_terminal_and_exact(self):
        self.prepare()
        with self.assertRaises(WorkflowUnavailable):
            self.store.cancel_prepared_public(self.proposal, "2026-09-07T10:00:00Z")
        self.assertTrue(self.store.cancel_prepared_public(self.proposal, DATE))
        self.assertTrue(self.open().cancel_prepared_public(self.proposal, DATE))
        self.assertEqual(self.open().pending_count(), 0)
        self.assertTrue(self.open().reconcile_public(OPERATION)["cancelled"])
        self.assertFalse(self.open().reconcile_public(OPERATION)["reserved"])
        with self.assertRaises(WorkflowUnavailable):
            self.open().sdk_journal.reserve(OPERATION, self.proposal.content_hash)
        with self.assertRaises(WorkflowUnavailable):
            self.open().prepare_public(self.proposal, DATE)

    def test_preparation_cancel_never_cancels_reserved_or_receipted(self):
        self.prepare()
        self.store.sdk_journal.reserve(OPERATION, self.proposal.content_hash)
        with self.assertRaises(WorkflowUnavailable):
            self.store.cancel_prepared_public(self.proposal, DATE)
        self.assertEqual(self.store.pending_count(), 1)
        self.store.sdk_journal.complete(OPERATION, self.receipt)
        with self.assertRaises(WorkflowUnavailable):
            self.store.cancel_prepared_public(self.proposal, DATE)
        self.assertEqual(self.store.reconcile_public(OPERATION)["receipt"], self.receipt)

    def test_cancel_cas_loses_to_concurrent_reservation(self):
        self.prepare()
        original_put = self.workflow.put
        def racing_put(identifier, kind, revision, payload, **kwargs):
            if payload["result"] == {"cancelled": True}:
                self.workflow.put = original_put
                self.store.sdk_journal.reserve(OPERATION, self.proposal.content_hash)
            return original_put(identifier, kind, revision, payload, **kwargs)
        self.workflow.put = racing_put
        with self.assertRaises(WorkflowOutcomeUnknown):
            self.store.cancel_prepared_public(self.proposal, DATE)
        self.assertEqual(self.store.pending_count(), 1)
        self.assertFalse(self.store.reconcile_public(OPERATION)["cancelled"])

    def test_default_scan_covers_actor_registry_before_filtering(self):
        self.prepare()
        for index in range(4095):
            identifier = str(uuid.UUID(int=index + 100))
            self.workflow.rows[identifier] = dict(record_id=identifier, kind="conversation_session", payload={})
        self.assertEqual(self.store.pending_count(), 1)
        self.assertEqual(self.store.list_public(), [])
        self.assertIn(("scan", 4096), self.workflow.calls)
        self.workflow.rows[str(uuid.UUID(int=99999))] = dict(kind="conversation_control", payload={})
        with self.assertRaises(WorkflowUnavailable):
            self.store.pending_count()

    def test_scan_lost_drain_fence_never_returns_zero(self):
        original_scan = self.workflow.scan
        def scan(**kwargs):
            rows = original_scan(**kwargs)
            self.drain = False
            return rows
        self.workflow.scan = scan
        with self.assertRaises(WorkflowUnavailable):
            self.store.pending_count()

    def test_provenance_hash_timestamp_and_receipt_conflicts_rejected(self):
        self.prepare()
        for proposal, date in ((replace(self.proposal, author="f" * 64), DATE),
                               (replace(self.proposal, content_hash="f" * 64), DATE),
                               (self.proposal, "2026-09-07T10:00:00Z")):
            with self.subTest(proposal=proposal.operation_id, date=date), self.assertRaises(WorkflowUnavailable):
                self.store.prepare_public(proposal, date)
        self.store.sdk_journal.reserve(OPERATION, self.proposal.content_hash)
        for field, value in (("operation_id", MEMORY), ("content_hash", "f" * 64),
                             ("intended_chain_id", "wrong"), ("committed_height", True), ("tx_hash", "bad")):
            with self.subTest(field=field), self.assertRaises(WorkflowUnavailable):
                self.store.sdk_journal.complete(OPERATION, replace(self.receipt, **{field: value}))
        self.store.sdk_journal.complete(OPERATION, self.receipt)
        self.assertTrue(self.store.sdk_journal.complete(OPERATION, self.receipt))
        with self.assertRaises(WorkflowUnavailable):
            self.store.sdk_journal.complete(OPERATION, replace(self.receipt, committed_height=5))

    def test_unknown_reservation_does_not_grant_resubmission(self):
        self.prepare()
        self.workflow.lose_ack = True
        with self.assertRaises(WorkflowOutcomeUnknown):
            self.store.sdk_journal.reserve(OPERATION, self.proposal.content_hash)
        self.workflow.lose_ack = False
        reopened = self.open()
        self.assertFalse(reopened.sdk_journal.reserve(OPERATION, self.proposal.content_hash))
        self.assertTrue(reopened.reconcile_public(OPERATION)["reserved"])
        self.assertEqual(reopened.pending_count(), 1)

    def test_unknown_completion_reconciles_exact_receipt_without_second_write(self):
        self.prepare()
        self.store.sdk_journal.reserve(OPERATION, self.proposal.content_hash)
        self.workflow.lose_ack = True
        with self.assertRaises(WorkflowOutcomeUnknown):
            self.store.sdk_journal.complete(OPERATION, self.receipt)
        writes = sum(call[0] == "put" for call in self.workflow.calls)
        self.workflow.lose_ack = False
        reopened = self.open()
        self.assertEqual(reopened.reconcile_public(OPERATION)["receipt"], self.receipt)
        self.assertTrue(reopened.record_public(self.proposal, self.receipt, DATE))
        self.assertEqual(sum(call[0] == "put" for call in self.workflow.calls), writes)

    def test_unknown_prepare_reconciles_but_does_not_reserve(self):
        self.workflow.lose_ack = True
        with self.assertRaises(WorkflowOutcomeUnknown):
            self.store.prepare_public(self.proposal, DATE)
        self.workflow.lose_ack = False
        self.assertTrue(self.open().prepare_public(self.proposal, DATE))
        self.assertFalse(self.open().reconcile_public(OPERATION)["reserved"])

    def test_http_fingerprint_and_terminal_tombstones_immutable(self):
        self.store.reserve(OPERATION, "d" * 64)
        self.assertFalse(self.open().reserve(OPERATION, "d" * 64))
        with self.assertRaises(WorkflowUnavailable):
            self.store.reserve(OPERATION, "e" * 64)
        self.store.complete(OPERATION, {"cancelled": True})
        self.assertEqual(self.open().get(OPERATION), dict(fingerprint="d" * 64, result={"cancelled": True}))
        with self.assertRaises(WorkflowUnavailable):
            self.store.complete(OPERATION, {"id": MEMORY})

    def test_auxiliary_http_results_cannot_assert_public_acceptance(self):
        self.store.reserve(OPERATION, "d" * 64)
        for result in ({"cancelled": 1}, {"id": MEMORY, "state": "accepted", "created_at": DATE},
                       {"id": MEMORY, "state": "pending", "created_at": DATE, "verified": True}):
            with self.subTest(result=result), self.assertRaises(WorkflowUnavailable):
                self.store.complete(OPERATION, result)
        self.assertIsNone(self.store.get(OPERATION)["result"])

    def test_drain_lease_separate_from_user_access_and_unavailable_denies(self):
        self.prepare()
        self.allowed = False
        with self.assertRaises(WorkflowUnavailable):
            self.store.list_public()
        self.assertEqual(self.store.pending_count(), 1)
        self.drain = False
        with self.assertRaises(WorkflowUnavailable):
            self.store.pending_count()
        self.drain = True
        self.workflow.available = False
        with self.assertRaises(WorkflowUnavailable):
            self.store.pending_count()

    def test_scan_bound_malformed_rows_and_limits_fail_closed(self):
        self.prepare()
        self.store.reserve(OPERATION, "d" * 64)
        with self.assertRaises(WorkflowUnavailable):
            self.open(max_records=1).pending_count()
        for limit in (0, 22, True):
            with self.assertRaises(WorkflowUnavailable):
                self.store.list_public(limit=limit)
        row = next(iter(self.workflow.rows.values()))
        row["payload"]["fingerprint"] = "malformed"
        with self.assertRaises(WorkflowUnavailable):
            self.store.pending_count()

    def test_tampered_binding_cannot_hide_a_pending_row(self):
        self.prepare()
        row = next(iter(self.workflow.rows.values()))
        row["payload"]["binding"]["domain"] = "elsewhere"
        with self.assertRaises(WorkflowUnavailable):
            self.store.pending_count()

    def test_cas_race_admits_only_one_sdk_submit(self):
        self.prepare()
        barrier = threading.Barrier(2)
        original_get = self.workflow.get
        def racing_get(*args, **kwargs):
            row = original_get(*args, **kwargs)
            barrier.wait(timeout=2)
            return row
        self.workflow.get = racing_get
        outcomes = []
        def reserve():
            try:
                outcomes.append(self.open().sdk_journal.reserve(OPERATION, self.proposal.content_hash))
            except WorkflowOutcomeUnknown:
                outcomes.append("unknown")
        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, [True, "unknown"])

    def test_public_history_newest_first_and_other_scope_isolated(self):
        for index in range(3):
            identifier = str(uuid.UUID(int=index + 10))
            proposal = self.public.prepare(operation_id=identifier, question="Question", answer="Answer")
            date = f"2026-09-0{index + 1}T10:00:00Z"
            receipt = replace(self.receipt, operation_id=identifier, content_hash=proposal.content_hash,
                              memory_id=str(uuid.UUID(int=index + 20)))
            self.store.prepare_public(proposal, date)
            self.store.sdk_journal.reserve(identifier, proposal.content_hash)
            self.store.sdk_journal.complete(identifier, receipt)
        self.assertEqual(self.open().list_public(limit=1)[0]["created_at"], "2026-09-03T10:00:00Z")
        other = PublicWorkflowStore(self.workflow, chain_id="other", domain="commons", author="b" * 64,
                                    access_check=lambda: True, drain_check=lambda: True)
        self.assertEqual(other.list_public(), [])
        with self.assertRaises(WorkflowNotFound):
            other.reconcile_public(str(uuid.UUID(int=10)))

    def test_oversize_proposal_fails_before_any_put(self):
        proposal = self.public.prepare(operation_id=OPERATION, question="x" * 4096, answer="y" * 8192)
        self.store.prepare_public(proposal, DATE)
        self.assertEqual(len(self.workflow.rows), 1)
        proposal = self.public.prepare(operation_id=MEMORY, question="é" * 1800, answer="é" * 3500)
        with self.assertRaises(WorkflowUnavailable):
            self.store.prepare_public(proposal, DATE)
        self.assertEqual(len(self.workflow.rows), 1)


def test_real_sdk_workflow_history_and_unknown_completion(signed_workflow):
    fixture = signed_workflow
    def opened():
        return PublicWorkflowStore(fixture.store, chain_id="public", domain="commons", author="b" * 64,
                                   access_check=lambda: True, drain_check=lambda: True)
    store = opened()
    public = CommunityPublic(None, store.sdk_journal, chain_id="public", domain="commons", author="b" * 64)
    proposal = public.prepare(operation_id=OPERATION, question="Question", answer="Answer")
    receipt = PublicReceipt(OPERATION, proposal.content_hash, "public", MEMORY, "c" * 64, 4)
    store.prepare_public(proposal, DATE)
    store.sdk_journal.reserve(OPERATION, proposal.content_hash)
    fixture.put_status = "timeout"
    with pytest.raises(WorkflowOutcomeUnknown):
        store.sdk_journal.complete(OPERATION, receipt)
    fixture.put_status = 200
    reopened = opened()
    assert reopened.reconcile_public(OPERATION)["receipt"] == receipt
    assert reopened.list_public() == [dict(proposal=proposal, receipt=receipt, created_at=DATE)]
    assert reopened.pending_count() == 0
    assert all(row["kind"] == "public_proposal" for row in fixture.records.values())
    assert not any("memories" in call[1] or "propose" in call[1] for call in fixture.calls)
    fixture.storage["vault_active"] = False
    with pytest.raises(WorkflowUnavailable):
        reopened.pending_count()
