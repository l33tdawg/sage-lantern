import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from sage_lantern.conversation_store import ConversationStore, ConversationStoreError


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve() / "private"
        self.store = ConversationStore(self.directory)
        self.addCleanup(lambda: self.store.close())
        self.item = dict(session_id=str(uuid4()), created_at="2026-09-05T10:00:00Z",
                         question="Where did I put my camera?", answer="On your desk.", has_photo=True,
                         reflection="The user asked about their camera.")
        self.key = "a" * 64
        self.fingerprint = "b" * 64

    def enqueue(self):
        return self.store.enqueue(self.store.approve(True), self.item)

    def reopen(self, **options):
        self.store.close()
        self.store = ConversationStore(self.directory, **options)

    def receipt(self, **changes):
        value = dict(operation_key=self.key, memory_id="memory-1", tx_hash="tx-1", status="proposed",
                     committed=True, committed_height=4488, projection_confirmed=False,
                     retryable=False, outcome="acknowledged")
        value.update(changes)
        return SimpleNamespace(**value)

    def reserve(self, operation_id):
        with self.store.bind_operation(operation_id):
            return self.store.reserve(self.key, self.fingerprint)

    def test_defaults_off_explicit_seed_only_and_no_legacy_import(self):
        self.assertIsNone(self.store.consent())
        self.assertEqual(self.store.enqueue(None, self.item), [])
        self.reopen(approved=True)
        self.assertIsNone(self.store.consent())
        self.assertEqual(self.store.list(), [])
        other = ConversationStore(self.directory.parent / "approved", approved=True)
        self.addCleanup(other.close)
        self.assertIsInstance(other.consent(), str)

    def test_modes_wal_full_and_reopen_payload(self):
        operations = self.enqueue()
        ticket = self.store.consent()
        self.assertEqual(self.store._db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.store._db.execute("PRAGMA synchronous").fetchone()[0], 2)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for path in self.directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
        before = self.store.pending()
        self.reopen()
        self.assertEqual(self.store.consent(), ticket)
        self.assertEqual(self.store.pending(), before)
        self.assertEqual(len(operations), 2)
        for operation in before:
            self.assertEqual(operation["payload_sha256"], hashlib.sha256(operation["payload"].encode()).hexdigest())
            self.assertEqual(json.loads(operation["payload"])["session_id"], self.item["session_id"])
            self.assertEqual(self.store.work(self.item["session_id"], operation["kind"]), operation)

    def test_duplicate_exact_payload_conflict_and_no_mutation(self):
        operations = self.enqueue()
        duplicate = self.store.enqueue(self.store.consent(), dict(reversed(list(self.item.items()))))
        self.assertEqual(set(operations), set(duplicate))
        for field, replacement in (("answer", "Changed"), ("reflection", "Changed"), ("has_photo", False)):
            with self.assertRaises(ConversationStoreError):
                self.store.enqueue(self.store.consent(), {**self.item, field: replacement})
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(len(self.store.operations()), 2)

    def test_optout_invalidates_both_generation_scopes(self):
        ticket = self.store.approve(True, history_generation="json-1")
        operations = self.store.enqueue(ticket, self.item)
        self.assertEqual(ticket["history_generation"], "json-1")
        self.assertEqual(self.store.approve(True, history_generation="json-1"), ticket)
        self.store.mark_dispatching(operations[0])
        self.store.approve(False, history_generation="json-2")
        self.assertEqual(self.store.operation(operations[0])["state"], "unknown")
        self.assertEqual(self.store.operation(operations[1])["state"], "cancelled")
        self.assertEqual(self.store.pending(), [])
        new_ticket = self.store.approve(True, history_generation="json-3")
        self.assertNotEqual(ticket, new_ticket)
        self.assertEqual(self.store.enqueue(ticket, {**self.item, "session_id": str(uuid4())}), [])
        self.reopen()
        self.assertEqual(self.store.consent(), new_ticket)
        self.assertEqual(len(self.store.list()), 1)

    def test_external_generation_invalidates_capture_not_durable_work(self):
        ticket = self.store.approve(True, history_generation="json-1")
        operations = self.store.enqueue(ticket, self.item)
        current = self.store.approve(True, history_generation="json-2")
        self.assertNotEqual(ticket, current)
        self.assertEqual(self.store.enqueue(ticket, {**self.item, "session_id": str(uuid4())}), [])
        self.assertEqual(len(self.store.pending()), 2)
        self.assertEqual(self.store.operation(operations[0])["consent_generation"], ticket["store_generation"])
        self.assertEqual(self.store.operation(operations[0])["history_generation"], "json-1")
        self.store.mark_dispatching(operations[0])
        self.assertTrue(self.reserve(operations[1]))
        self.reopen()
        self.assertEqual({row["state"] for row in self.store.operations()}, {"unknown"})

    def test_delete_rotation_preserves_unrelated_queue_and_payload(self):
        ticket = self.store.approve(True, history_generation="before-delete")
        deleted = self.store.enqueue(ticket, self.item)
        retained = self.store.enqueue(ticket, {**self.item, "session_id": str(uuid4())})
        before = self.store.operation(retained[0])
        self.store.suppress([self.item["session_id"]])
        self.store.approve(True, history_generation="after-delete")
        self.reopen()
        self.assertEqual({row["operation_id"] for row in self.store.pending()}, set(retained))
        self.assertEqual(self.store.operation(deleted[0])["state"], "cancelled")
        self.assertEqual(self.store.operation(retained[0]), before)
        self.assertTrue(self.reserve(retained[0]))

    def test_optout_reenable_does_not_revive_unknown_proven_unsent(self):
        operations = self.enqueue()
        self.store.mark_dispatching(operations[0])
        self.store.approve(False)
        self.store.approve(True)
        self.reopen()
        self.store.reconcile_unknown(operations[0], proven_not_submitted=True)
        self.assertEqual(self.store.pending(), [])
        self.assertEqual({row["state"] for row in self.store.operations()}, {"cancelled"})

    def test_history_refresh_does_not_revoke_unknown_proven_unsent(self):
        operations = self.enqueue()
        self.store.mark_dispatching(operations[0])
        self.store.mark_unknown(operations[0])
        self.store.approve(True, history_generation="photo-change")
        self.store.reconcile_unknown(operations[0], proven_not_submitted=True)
        self.assertEqual(len(self.store.pending()), 2)

    def test_reopen_dispatching_is_unknown_not_queued(self):
        operations = self.enqueue()
        self.store.mark_dispatching(operations[0])
        self.reopen()
        self.assertEqual(self.store.operation(operations[0])["state"], "unknown")
        self.assertEqual([row["operation_id"] for row in self.store.pending()], [operations[1]])
        self.assertEqual(len(self.store.unresolved()), 1)
        with self.assertRaises(ConversationStoreError):
            self.store.mark_dispatching(operations[0])
        with self.assertRaises(ConversationStoreError):
            self.store.reconcile_unknown(operations[0])
        self.store.reconcile_unknown(operations[0], proven_not_submitted=True)
        self.assertEqual(self.store.operation(operations[0])["state"], "queued")

    def test_suppression_delete_race_and_late_receipt(self):
        operations = self.enqueue()
        self.store.mark_dispatching(operations[0])
        self.store.suppress([self.item["session_id"]])
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.operations(), [])
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.operation(operations[0])["state"], "unknown")
        self.assertEqual(self.store.operation(operations[1])["state"], "cancelled")
        self.store.reconcile_unknown(operations[0], memory_id="memory-1")
        self.store.mark_deprecated(operations[0], "challenged")
        self.assertEqual(self.store.operation(operations[0])["deprecation_status"], "challenged")
        self.store.mark_deprecated(operations[0], "deprecated")
        self.reopen()
        self.assertEqual(self.store.suppressed_ids(), {self.item["session_id"]})
        self.assertEqual(self.store.enqueue(self.store.consent(), self.item), [])
        self.assertTrue(self.store.list(include_suppressed=True)[0]["suppressed"])
        self.assertEqual(self.store.operations("accepted", True)[0]["deprecation_status"], "deprecated")

    def test_unknown_suppressed_proven_unsent_stays_cancelled(self):
        operation_id = self.enqueue()[0]
        self.store.mark_dispatching(operation_id)
        self.store.suppress([self.item["session_id"]])
        self.store.reconcile_unknown(operation_id, proven_not_submitted=True)
        self.assertEqual(self.store.operation(operation_id)["state"], "cancelled")

    def test_tombstone_before_enqueue_and_idempotent_suppress(self):
        self.store.suppress([self.item["session_id"]])
        self.store.suppress([self.item["session_id"]])
        self.assertEqual(self.enqueue(), [])
        self.reopen()
        self.assertTrue(self.store.is_suppressed(self.item["session_id"]))

    def test_atomic_rollback_interruption_during_enqueue(self):
        self.store.approve(True)
        self.store._db.execute("CREATE TRIGGER interrupt_insert BEFORE INSERT ON operations BEGIN SELECT RAISE(ABORT, 'interrupt'); END")
        with self.assertRaises(ConversationStoreError):
            self.store.enqueue(self.store.consent(), self.item)
        self.assertEqual(self.store.list(), [])
        self.reopen()
        self.assertEqual(self.store.operations(), [])

    def test_atomic_rollback_interruption_during_suppression(self):
        self.enqueue()
        self.store._db.execute("CREATE TRIGGER interrupt_update BEFORE UPDATE ON operations BEGIN SELECT RAISE(ABORT, 'interrupt'); END")
        with self.assertRaises(ConversationStoreError):
            self.store.suppress([self.item["session_id"]])
        self.assertEqual(self.store.suppressed_ids(), set())
        self.assertEqual(len(self.store.pending()), 2)

    def test_owner_lock_blocks_second_controller(self):
        with self.assertRaises(ConversationStoreError):
            ConversationStore(self.directory)
        self.assertIsNone(self.store.consent())

    def test_empty_or_missing_existing_database_never_resets_consent(self):
        self.enqueue()
        self.store.close()
        path = self.directory / "conversations.sqlite3"
        path.write_bytes(b"")
        with self.assertRaises(ConversationStoreError):
            ConversationStore(self.directory, approved=True)
        path.unlink()
        with self.assertRaises(ConversationStoreError):
            ConversationStore(self.directory, approved=True)

    def test_valid_sqlite_without_settings_fails_closed(self):
        self.store._db.execute("DELETE FROM settings")
        with self.assertRaises(ConversationStoreError):
            self.store.consent()
        self.store.close()
        with self.assertRaises(ConversationStoreError):
            ConversationStore(self.directory, approved=True)

    def test_file_permissions_symlink_hardlink_and_sidecar_rejected(self):
        self.store.close()
        path = self.directory / "conversations.sqlite3"
        path.chmod(0o644)
        with self.assertRaises(ConversationStoreError):
            ConversationStore(self.directory)
        path.chmod(0o600)
        linked = self.directory.parent / "linked"
        os.link(path, linked)
        with self.assertRaises(ConversationStoreError):
            ConversationStore(self.directory)
        linked.unlink()
        moved = self.directory.parent / "moved"
        path.rename(moved)
        path.symlink_to(moved)
        with self.assertRaises(ConversationStoreError):
            ConversationStore(self.directory)
        path.unlink()
        moved.rename(path)
        sidecar = self.directory / "conversations.sqlite3-wal"
        sidecar.symlink_to(path)
        with self.assertRaises(ConversationStoreError):
            ConversationStore(self.directory)

    def test_directory_symlink_and_owner_mismatch_rejected(self):
        alias = self.directory.parent / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ConversationStoreError):
            ConversationStore(alias)
        with patch("sage_lantern.conversation_store.os.geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(ConversationStoreError):
                self.store.consent()

    def test_capacity_invalid_item_and_no_retention(self):
        self.item["created_at"] = "2000-01-01T00:00:00Z"
        self.enqueue()
        self.assertEqual(len(self.store.list()), 1)
        with patch("sage_lantern.conversation_store.MAX_ENTRIES", 1):
            with self.assertRaises(ConversationStoreError):
                self.store.enqueue(self.store.consent(), {**self.item, "session_id": str(uuid4())})
        for changes in ({"question": "x" * 20000}, {"created_at": "2026-01-01"}, {"has_photo": 1}, {"session_id": "../unsafe"}):
            with self.assertRaises(ConversationStoreError):
                self.store.enqueue(self.store.consent(), {**self.item, **changes})

    def test_journal_reserved_before_send_never_replays_after_reopen(self):
        operation_id = self.enqueue()[0]
        self.assertTrue(self.reserve(operation_id))
        self.assertFalse(self.reserve(operation_id))
        self.assertIsNone(self.store.get(self.key).receipt)
        self.reopen()
        self.assertFalse(self.reserve(operation_id))
        self.assertEqual(self.store.operation(operation_id)["state"], "unknown")
        with self.assertRaises(ConversationStoreError):
            self.store.reconcile_unknown(operation_id, proven_not_submitted=True)
        self.store.complete(self.key, self.receipt())
        self.assertEqual(self.store.operation(operation_id)["state"], "accepted")
        self.assertEqual(self.store.get(self.key).receipt.memory_id, "memory-1")

    def test_parallel_reserve_only_one_fresh(self):
        operation_id = self.enqueue()[0]
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda unused: self.reserve(operation_id), range(12)))
        self.assertEqual(results.count(True), 1)

    def test_journal_binding_conflict_and_optout_gate(self):
        operations = self.enqueue()
        with self.assertRaises(ConversationStoreError):
            self.store.reserve(self.key, self.fingerprint)
        self.reserve(operations[0])
        with self.assertRaises(ConversationStoreError):
            self.reserve(operations[1])
        with self.assertRaises(ConversationStoreError):
            self.store.reserve(self.key, "c" * 64)
        self.store.approve(False)
        with self.store.bind_operation(operations[1]):
            with self.assertRaises(ConversationStoreError):
                self.store.reserve("d" * 64, self.fingerprint)

    def test_journal_complete_poll_and_suppression_preserve_provenance(self):
        operation_id = self.enqueue()[0]
        self.reserve(operation_id)
        self.store.complete(self.key, self.receipt())
        self.store.complete(self.key, self.receipt())
        self.assertEqual(self.store.operation(operation_id)["state"], "accepted")
        self.store.complete(self.key, self.receipt(tx_hash="", committed=None, committed_height=None,
                            status="committed", projection_confirmed=True, outcome="projection_observed"))
        self.assertEqual(self.store.operation(operation_id)["state"], "recall_ready")
        self.assertEqual(self.store.get(self.key).receipt.tx_hash, "tx-1")
        with self.assertRaises(ConversationStoreError):
            self.store.complete(self.key, self.receipt(memory_id="different"))
        self.store.suppress([self.item["session_id"]])
        self.store.mark_deprecated(operation_id, "deprecated")
        self.reopen()
        self.assertEqual(self.store.operation(operation_id)["deprecation_status"], "deprecated")
        self.assertEqual(self.store.operations("recall_ready"), [])

    def test_late_journal_receipt_after_delete_stays_suppressed(self):
        operation_id = self.enqueue()[0]
        self.reserve(operation_id)
        self.store.suppress([self.item["session_id"]])
        self.store.complete(self.key, self.receipt())
        self.assertTrue(self.store.operation(operation_id)["suppressed"])
        self.assertEqual(self.store.operations(), [])
        self.assertEqual(self.store.operations("accepted", True)[0]["memory_id"], "memory-1")

    def test_payload_corruption_and_deleted_database_fail_closed(self):
        operation_id = self.enqueue()[0]
        self.store._db.execute("UPDATE operations SET payload='changed' WHERE operation_id=?", (operation_id,))
        with self.assertRaises(ConversationStoreError):
            self.store.pending()
        (self.directory / "conversations.sqlite3").unlink()
        with self.assertRaises(ConversationStoreError):
            self.store.approve(True)

    def test_adapter_actual_reserve_complete_and_unknown_replay(self):
        from sage_lantern.conversation_sage import ConversationSage

        operations = self.enqueue()
        profile = SimpleNamespace(agent_id="voiceidentity", role="member", clearance=1,
                                  enrollment_status="active", registration_status="active",
                                  home_domain="local-field-knowledge", approval_required=False,
                                  can_read=True, can_write=True)
        access = {name: ["local-field-knowledge"] for name in ("owned_domains", "readable_domains", "writable_domains")}
        client = SimpleNamespace(get_profile=lambda: profile, domain_access_sample=lambda: access,
                                 embed=lambda text: [0.1, 0.2], propose=Mock(return_value=self.receipt()))
        adapter = ConversationSage(client, agent_id="voiceidentity", domain="local-field-knowledge",
                                   classification=1, journal=self.store, ledger_consent=True,
                                   is_tombstoned=self.store.is_suppressed)
        operation = self.store.operation(operations[0])
        with self.store.bind_operation(operations[0]):
            receipt = adapter.record_turn(operation["session_id"], "interaction", operation["payload"])
            repeated = adapter.record_turn(operation["session_id"], "interaction", operation["payload"])
        self.assertEqual(receipt.memory_id, repeated.memory_id)
        self.assertEqual(client.propose.call_count, 1)
        self.assertEqual(self.store.operation(operations[0])["state"], "accepted")
        operation = self.store.operation(operations[1])
        client.propose.side_effect = TimeoutError("lost response")
        with self.store.bind_operation(operations[1]):
            receipt = adapter.reflect(operation["session_id"], "reflection", operation["payload"])
        self.assertEqual(receipt.outcome, "unknown")
        self.reopen()
        adapter.journal = self.store
        adapter.is_tombstoned = self.store.is_suppressed
        with self.store.bind_operation(operations[1]):
            receipt = adapter.reflect(operation["session_id"], "reflection", operation["payload"])
        self.assertEqual(receipt.outcome, "unknown")
        self.assertEqual(client.propose.call_count, 2)
        self.assertEqual(self.store.operation(operations[1])["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
