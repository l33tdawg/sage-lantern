"""Synthetic SDK clients and in-memory journal doubles; no real keys or IO."""

import copy
import hashlib
import json
import threading
import unittest
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from sage_lantern.community_messaging import CommunityMessaging, Prerequisites
from sage_lantern.community_public import CANONICAL_DISCLOSURE, CommunityPublic, OnlineNodeTrust
from sage_lantern.community_service import CommunityError, CommunityService, MAX_RESPONSE
from sage_lantern.message_contacts import ContactClaim, RecipientID


SOURCE = "c527836c-5a47-4a0a-b8d6-2e8b4fe7d4f6"
REQUEST = "1ec9a2e2-0e7f-47e9-919c-a7ca2d22e1c4"
MEMORY = "f8267d57-1bc2-45f1-a5eb-543d066ee62e"
SNAPSHOT = {"question": "Why blue?", "answer": "Scattered light.", "has_photo": True}


class JournalDouble:
    def __init__(self):
        self.rows = {}
        self.public_rows = {}
        self.prepared = {}
        self.ready = True

    def available(self):
        return self.ready

    def reserve(self, key, fingerprint):
        if key in self.rows:
            return False
        self.rows[key] = {"fingerprint": fingerprint, "result": None}
        return True

    def get(self, key):
        return copy.deepcopy(self.rows[key])

    def complete(self, key, receipt):
        self.rows[key]["result"] = copy.deepcopy(receipt)
        return True

    def pending_count(self):
        return sum(row["result"] is None for row in self.rows.values())

    def record_public(self, proposal, receipt, created_at):
        entry = dict(proposal=proposal, receipt=receipt, created_at=created_at)
        if receipt.memory_id in self.public_rows and self.public_rows[receipt.memory_id] != entry:
            raise ValueError("conflicting receipt")
        self.public_rows[receipt.memory_id] = copy.deepcopy(entry)
        return True

    def prepare_public(self, proposal, created_at):
        entry = (proposal, created_at)
        if proposal.operation_id in self.prepared and self.prepared[proposal.operation_id] != entry:
            raise ValueError("conflicting preparation")
        self.prepared[proposal.operation_id] = entry
        return True

    def list_public(self, *, limit):
        return copy.deepcopy(list(self.public_rows.values())[:limit])


class CommunityServiceTests(unittest.TestCase):
    def setUp(self):
        self.actor = RecipientID("private-chain", "a" * 64)
        self.peer = RecipientID("private-chain", "b" * 64)
        self.claim = ContactClaim(self.actor, self.peer, "Peer", "issuer", "policy", 1, 95, 150, True, True)
        self.sdk = Mock()
        self.sdk.lookup_agents.return_value = {"agents": [{"agent_id": self.peer.agent_id, "status": "active"}]}
        self.sdk.pipe_resolve.return_value = {"to_agent": self.peer.agent_id}
        self.sdk.message_send.return_value = {"message_id": "message-1", "status": "pending", "idempotent_replay": False}
        self.sdk.pipe_inbox_history.return_value = {"items": [], "count": 0}
        self.sdk.pipe_outbox.return_value = {"items": [], "count": 0}
        self.prerequisites = Prerequisites(self.actor, True, True, True, True, True, "issuer", "policy")
        self.messaging = CommunityMessaging(self.sdk, actor=self.actor, prerequisites=lambda: self.prerequisites,
                                            guard=lambda request: True, verify_recipient=lambda *args: self.claim,
                                            clock=lambda: 100)
        self.journal = JournalDouble()
        self.public_sdk = Mock()
        self.public_sdk.get_profile.return_value = dict(agent_id="c" * 64, role="member", enrollment_status="active",
            registration_status="active", approval_required=False, can_read=True, can_write=True, clearance=0, home_domain="commons")
        self.public_sdk.propose.return_value = dict(status="committed", memory_id=MEMORY, tx_hash="d" * 64, committed_height=12)
        self.public = CommunityPublic(self.public_sdk, self.journal, chain_id="public-chain", domain="commons", author="c" * 64)
        self.source = Mock(return_value=dict(SNAPSHOT))
        self.allowed = True
        self.session_id = "synthetic-auth-session-a"
        self.now = 100.0
        self.service = CommunityService(community_messaging=self.messaging, community_public=self.public,
            protected_store=self.journal, source_fetch=self.source, contact_names=("Peer",),
            public_readiness=lambda: "ready", clock=lambda: self.now, wall_clock=lambda: 1700000000)

    def call(self, route, body=None, service=None):
        return (service or self.service).dispatch("/community/" + route, {} if body is None else body,
                                                lambda: self.allowed, session_id=self.session_id)

    def preview(self, service=None):
        return self.call("shares/preview", {"source_id": SOURCE, **SNAPSHOT}, service)

    def test_source_status_exact_order_unknown_and_unavailable(self):
        other = str(uuid.uuid4())
        with self.assertRaises(CommunityError):
            self.call("shares/status", {"source_ids": [SOURCE]})
        self.journal.public_sources = lambda ids: {value: None for value in ids}
        self.assertEqual(self.call("shares/status", {"source_ids": [other, SOURCE]}),
            {"items": [{"source_id": other, "state": "not_shared"}, {"source_id": SOURCE, "state": "not_shared"}]})
        self.journal.public_sources = lambda ids: {}
        with self.assertRaises(CommunityError):
            self.call("shares/status", {"source_ids": [SOURCE]})
        for ids in ([SOURCE, SOURCE], [str(uuid.uuid4()) for _ in range(21)], [True], ["bad"], None):
            with self.subTest(ids=ids), self.assertRaises(CommunityError):
                self.call("shares/status", {"source_ids": ids})

    def test_source_status_uses_verified_online_state_after_lock(self):
        self.test_verified_online_public_states_and_revoked_trust()
        entry = next(iter(self.journal.public_rows.values()))
        self.journal.public_sources = lambda ids: {SOURCE: entry}
        self.service.lock()
        self.session_id = "new-session"
        self.assertEqual(self.call("shares/status", {"source_ids": [SOURCE]}),
                         {"items": [{"source_id": SOURCE, "state": "unknown"}]})
        self.public._online_verifier.return_value = OnlineNodeTrust(self.public_sdk,
            "https://community.example", "public-chain", "reviewed-revision", True, CANONICAL_DISCLOSURE)
        self.public_sdk.get_memory.return_value["status"] = "committed"
        self.assertEqual(self.call("shares/status", {"source_ids": [SOURCE]})["items"][0]["state"], "accepted")

    def send_body(self):
        return dict(recipient_id=self.peer.agent_id, paragraphs=["Hello", "Private message"], request_id=REQUEST, consent=True)

    def test_missing_providers_false_capabilities_no_side_effects(self):
        result = self.call("status", service=CommunityService())
        self.assertEqual(result, {"schema": "sage.lantern.community.v1", "messaging": {"available": False, "reason": "not_ready"},
                                 "sharing": {"available": False, "reason": "not_ready"}, "dictation": {"available": False}})
        self.sdk.assert_not_called()
        self.public_sdk.assert_not_called()

    def test_exact_ready_status_and_empty_read_lists(self):
        result = self.call("status")
        self.assertEqual(result["messaging"], {"available": True, "reason": "ready"})
        self.assertEqual(result["sharing"], {"available": True, "reason": "ready"})
        for box in ("inbox", "outbox"):
            self.assertEqual(self.call("messages/list", {"box": box}), {"items": [], "more": False})

    def test_contacts_use_real_adapter_and_exact_wire_shape(self):
        self.assertEqual(self.call("contacts/list"), {"items": [{"id": self.peer.agent_id, "name": "Peer"}], "more": False})
        self.sdk.lookup_agents.assert_called_once_with(name="Peer", limit=21)

    def test_no_protected_store_allows_memory_preview_but_denies_mutations(self):
        service = CommunityService(community_public=self.public, source_fetch=self.source)
        preview = self.preview(service)
        self.assertEqual(self.call("status", service=service)["sharing"],
                         {"available": False, "reason": "encrypted_storage_unavailable"})
        with self.assertRaises(CommunityError):
            self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True), service)
        self.assertEqual(self.journal.rows, {})
        self.public_sdk.propose.assert_not_called()

    def test_preview_exact_source_bound_bytes_and_no_photo(self):
        preview = self.preview()
        self.assertEqual(set(preview), {"preview_id", "digest", "question", "answer", "photo_included", "expires_at"})
        self.assertFalse(preview["photo_included"])
        proposal = self.public.prepare(operation_id=preview["preview_id"], question=SNAPSHOT["question"], answer=SNAPSHOT["answer"])
        self.assertEqual(preview["digest"], hashlib.sha256(proposal.content.encode()).hexdigest())
        self.assertNotIn(SOURCE, proposal.content)
        self.assertNotIn("has_photo", proposal.content)
        self.source.assert_called_once_with(SOURCE)
        self.assertEqual(self.journal.rows, {})
        preview["answer"] = "mutated client copy"
        self.assertEqual(self.service._previews[preview["preview_id"]]["response"]["answer"], SNAPSHOT["answer"])

    def test_client_source_assertions_never_override_server(self):
        with self.assertRaises(CommunityError) as caught:
            self.call("shares/preview", {"source_id": SOURCE, **SNAPSHOT, "answer": "Injected"})
        self.assertEqual(caught.exception.code, "conflict")
        self.public_sdk.propose.assert_not_called()

    def test_real_public_submit_exact_preview_and_no_accepted_inference(self):
        preview = self.preview()
        response = self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True))
        self.assertEqual(response, dict(id=MEMORY, state="pending", created_at="2023-11-14T22:13:20Z"))
        content = self.public_sdk.propose.call_args.kwargs["content"]
        self.assertEqual(hashlib.sha256(content.encode()).hexdigest(), preview["digest"])
        self.assertEqual(self.public_sdk.propose.call_args.kwargs["classification"], 0)
        self.assertTrue(self.service.close())

    def test_real_message_send_and_same_id_retry_no_second_sdk_send(self):
        self.call("contacts/list")
        body = self.send_body()
        first = self.call("messages/send", body)
        self.assertEqual(first, dict(id="message-1", state="sent", created_at="2023-11-14T22:13:20Z"))
        self.assertEqual(first, self.call("messages/send", body))
        self.sdk.message_send.assert_called_once_with(to_agent=self.peer.agent_id, payload="Hello\n\nPrivate message",
            idempotency_key=REQUEST, ttl_minutes=1440)

    def test_changed_request_id_payload_conflicts(self):
        self.call("contacts/list")
        self.call("messages/send", self.send_body())
        with self.assertRaises(CommunityError) as caught:
            self.call("messages/send", {**self.send_body(), "paragraphs": ["different"]})
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(self.sdk.message_send.call_count, 1)

    def test_locked_before_admission_does_not_call_provider(self):
        self.allowed = False
        with self.assertRaises(CommunityError) as caught:
            self.call("contacts/list")
        self.assertEqual(caught.exception.code, "device_locked")
        self.sdk.lookup_agents.assert_not_called()

    def test_lock_discards_previews_contacts_and_invalidates_old_preview(self):
        preview = self.preview()
        self.call("contacts/list")
        self.service.lock()
        self.assertEqual(self.service._previews, {})
        self.assertEqual(self.service._contacts, {})
        with self.assertRaises(CommunityError):
            self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True))
        self.public_sdk.propose.assert_not_called()

    def test_preview_expiry_and_clock_rollback(self):
        self.preview()
        self.now += 301
        self.call("status")
        self.assertEqual(self.service._previews, {})
        self.preview()
        self.now -= 1
        with self.assertRaises(CommunityError):
            self.call("status")
        self.assertEqual(self.service._previews, {})

    def test_preview_capacity_bounded(self):
        for _ in range(32):
            self.preview()
        with self.assertRaises(CommunityError) as caught:
            self.preview()
        self.assertEqual(caught.exception.code, "busy")

    def test_lock_during_recipient_resolution_suppresses_sdk_send(self):
        self.call("contacts/list")
        def resolved(**kwargs):
            self.service.lock()
            return {"to_agent": self.peer.agent_id}
        self.sdk.pipe_resolve.side_effect = resolved
        with self.assertRaises(CommunityError):
            self.call("messages/send", self.send_body())
        self.sdk.message_send.assert_not_called()

        self.assertEqual(self.journal.rows[REQUEST]["result"], {"cancelled": True})
        self.assertTrue(self.service.close())

    def test_lock_after_dispatch_marks_unknown_and_blocks_clean_drain(self):
        self.call("contacts/list")
        def sent(**kwargs):
            self.service.lock()
            return dict(message_id="message-1", status="pending", idempotent_replay=False)
        self.sdk.message_send.side_effect = sent
        with self.assertRaises(CommunityError) as caught:
            self.call("messages/send", self.send_body())
        self.assertEqual(caught.exception.code, "outcome_unknown")
        self.assertFalse(self.service.close())

    def test_unknown_remote_exception_is_sanitized_and_not_retried(self):
        self.call("contacts/list")
        self.sdk.message_send.side_effect = RuntimeError("credential-secret-private-text")
        with self.assertRaises(CommunityError) as caught:
            self.call("messages/send", self.send_body())
        self.assertEqual(str(caught.exception), "outcome_unknown")
        with self.assertRaises(CommunityError):
            self.call("messages/send", self.send_body())
        self.assertEqual(self.sdk.message_send.call_count, 1)
        self.assertFalse(self.service.close())

    def test_journal_completion_failure_is_not_sent_success(self):
        self.call("contacts/list")
        self.journal.complete = Mock(side_effect=RuntimeError("storage-secret"))
        with self.assertRaises(CommunityError) as caught:
            self.call("messages/send", self.send_body())
        self.assertEqual(caught.exception.code, "outcome_unknown")
        self.assertFalse(self.service.close())

    def test_close_detects_prior_unresolved_journal(self):
        self.journal.reserve(REQUEST, "a" * 64)
        self.assertFalse(self.service.close())

    def test_inflight_close_returns_false_and_late_preview_is_discarded(self):
        entered, release = threading.Event(), threading.Event()
        def fetch(source_id):
            entered.set()
            release.wait(2)
            return dict(SNAPSHOT)
        self.source.side_effect = fetch
        errors = []
        def work():
            try:
                self.preview()
            except CommunityError as error:
                errors.append(error.code)
        worker = threading.Thread(target=work)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.service.close(timeout=0))
            with self.assertRaises(CommunityError):
                self.call("status")
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ["device_locked"])
        self.assertEqual(self.service._previews, {})

    def test_hostile_payloads_and_unknown_fields_reject(self):
        for route, body in (
            ("status", {"token": "secret"}), ("messages/list", {"folder": "inbox"}),
            ("messages/open", {"id": "message-1", "share_read_receipt": True}),
            ("messages/send", {**self.send_body(), "consent": 1}),
            ("messages/send", {**self.send_body(), "paragraphs": ["x" * 2049]}),
            ("shares/preview", {"source_id": SOURCE, **SNAPSHOT, "answer": "bad\x00text"}),
            ("shares/preview", {"source_id": "../../private", **SNAPSHOT}),
        ):
            with self.subTest(route=route), self.assertRaises(CommunityError) as caught:
                self.call(route, body)
            self.assertEqual(caught.exception.code, "invalid_request")

    def test_reason_strings_cannot_echo_provider_diagnostics(self):
        self.service._public_readiness = lambda: "private diagnostic secret"
        self.assertEqual(self.call("status")["sharing"], {"available": False, "reason": "not_ready"})

    def test_read_open_never_requests_read_receipt(self):
        with self.assertRaises(CommunityError) as caught:
            self.call("messages/open", {"id": "absent", "share_read_receipt": False})
        self.assertEqual(caught.exception.code, "not_found")
        self.sdk.pipe_receipt_record.assert_not_called()

    def test_bounded_page_and_replayed_result_schema(self):
        page = self.service._page([{ "id": str(index), "text": "x" * 8192} for index in range(30)])
        self.assertEqual(len(page["items"]), 20)
        self.assertTrue(page["more"])
        self.assertLess(len(json.dumps(page).encode()), MAX_RESPONSE)
        self.call("contacts/list")
        self.call("messages/send", self.send_body())
        self.journal.rows[REQUEST]["result"]["private_key"] = "never echo"
        with self.assertRaises(CommunityError):
            self.call("messages/send", self.send_body())

    def test_server_vault_alone_cannot_enable_unbound_retries(self):
        self.service._store = None
        self.service._messaging_readiness = lambda: "ready"
        self.service._messaging_drained = lambda: True
        self.assertEqual(self.call("status")["messaging"], {"available": False, "reason": "encrypted_storage_unavailable"})
        self.assertEqual(self.call("status")["sharing"]["reason"], "encrypted_storage_unavailable")
        self.call("contacts/list")
        with self.assertRaises(CommunityError):
            self.call("messages/send", self.send_body())
        self.assertEqual(self.journal.rows, {})
        self.sdk.message_send.assert_not_called()
        self.assertTrue(self.service.close())

    def test_server_vault_path_requires_positive_gates_and_drain(self):
        self.service._store = None
        self.service._messaging_readiness = lambda: "encrypted_storage_unavailable"
        self.service._messaging_drained = lambda: False
        self.call("contacts/list")
        with self.assertRaises(CommunityError):
            self.call("messages/send", self.send_body())
        self.sdk.message_send.assert_not_called()
        self.assertFalse(self.service.close())

    def test_history_without_provider_date_fails_closed(self):
        self.sdk.pipe_inbox_history.return_value = {"items": [dict(pipe_id="message-1",
            from_agent=self.peer.agent_id, to_agent=self.actor.agent_id, payload="Hello", status="completed")], "count": 1}
        with self.assertRaises(CommunityError):
            self.call("messages/list", {"box": "inbox"})

    def test_response_dates_match_client_iso_contract(self):
        for stamp in ("20260101T12:00:00+00:00", "2026-01-01T12:00:00+0000", "2026-01-01T12:00:00", "2026-02-30T12:00:00Z"):
            with self.subTest(stamp=stamp), self.assertRaises(CommunityError):
                self.service._validate_response("/community/messages/send", dict(id="message-1", state="sent", created_at=stamp))

    def test_contact_cache_replaces_previous_results(self):
        self.call("contacts/list")
        self.sdk.lookup_agents.return_value = {"agents": []}
        self.assertEqual(self.call("contacts/list"), {"items": [], "more": False})
        self.assertEqual(self.service._contacts, {})

    def test_share_list_real_poll_guard_and_no_false_rejection(self):
        preview = self.preview()
        self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True))
        proposal = self.service._previews[preview["preview_id"]]["proposal"]
        for status, expected in (("committed", "pending"), ("challenged", "unknown"), ("deprecated", "unknown")):
            self.public_sdk.get_memory.return_value = dict(memory_id=MEMORY, submitting_agent=self.public.author,
                domain_tag=self.public.domain, memory_type="inference", classification=0,
                content=proposal.content, content_hash=proposal.content_hash, status=status)
            with self.subTest(status=status):
                result = self.call("shares/list")
                self.assertEqual(result, {"items": [dict(id=MEMORY, question=SNAPSHOT["question"],
                    state=expected, created_at="2023-11-14T22:13:20Z")], "more": False})

    def test_verified_online_public_states_and_revoked_trust(self):
        evidence = OnlineNodeTrust(self.public_sdk, "https://community.example", "public-chain",
                                   "reviewed-revision", True, CANONICAL_DISCLOSURE)
        verifier = Mock(return_value=evidence)
        self.public._online_verifier = verifier
        self.service = CommunityService(community_public=self.public, protected_store=self.journal,
            source_fetch=self.source, public_readiness=lambda: "ready", clock=lambda: self.now,
            wall_clock=lambda: 1700000000)
        preview = self.preview()
        self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True))
        proposal = self.service._previews[preview["preview_id"]]["proposal"]
        for status, expected in (("committed", "accepted"), ("challenged", "challenged"), ("deprecated", "deprecated")):
            self.public_sdk.get_memory.return_value = dict(memory_id=MEMORY, submitting_agent=self.public.author,
                domain_tag=self.public.domain, memory_type="inference", classification=0,
                content=proposal.content, content_hash=proposal.content_hash, status=status)
            with self.subTest(status=status):
                item = self.call("shares/list")["items"][0]
                self.assertEqual(item["state"], expected)
                self.assertEqual(set(item), {"id", "question", "state", "created_at"})
                refreshed = self.call("shares/refresh", dict(id=MEMORY, request_id=str(uuid.uuid4()), consent=True))
                self.assertEqual(refreshed["state"], expected)
                self.assertEqual(refreshed["trust_basis"], "trusted_node_online")
                self.assertIs(refreshed["acceptance_available"], True)
                self.assertIs(refreshed["consensus_verified"], False)
                self.assertIs(refreshed["cache_eligible"], False)
        self.assertTrue(all(call.args == (self.public_sdk,) for call in verifier.call_args_list))
        verifier.return_value = None
        self.assertEqual(self.call("shares/list")["items"][0]["state"], "unknown")

    def test_online_verifier_revoked_during_read_cannot_accept(self):
        evidence = OnlineNodeTrust(self.public_sdk, "https://community.example", "public-chain",
                                   "reviewed-revision", True, CANONICAL_DISCLOSURE)
        self.public._online_verifier = Mock(side_effect=[evidence, None])
        self.service = CommunityService(community_public=self.public, protected_store=self.journal,
            source_fetch=self.source, public_readiness=lambda: "ready", clock=lambda: self.now)
        preview = self.preview()
        self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True))
        self.assertEqual(self.call("shares/list")["items"][0]["state"], "unknown")

    def test_invalid_submit_preconditions_never_reserve(self):
        preview = self.preview()
        body = dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True)
        for invalid in ({**body, "preview_id": SOURCE}, {**body, "digest": "0" * 64}):
            with self.assertRaises(CommunityError):
                self.call("shares/submit", invalid)
            self.assertEqual(self.journal.rows, {})
        self.service._public_readiness = lambda: "public_identity_unavailable"
        with self.assertRaises(CommunityError):
            self.call("shares/submit", body)
        self.assertEqual(self.journal.rows, {})
        self.public_sdk.propose.assert_not_called()
        self.assertTrue(self.service.close())

    def test_cross_session_preview_contact_and_share_list_isolation(self):
        preview = self.preview()
        self.call("contacts/list")
        body = dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True)
        self.session_id = "synthetic-auth-session-b"
        with self.assertRaises(CommunityError):
            self.call("shares/submit", body)
        with self.assertRaises(CommunityError):
            self.call("messages/send", self.send_body())
        self.assertEqual(self.journal.rows, {})
        self.session_id = "synthetic-auth-session-a"
        self.call("shares/submit", body)
        self.session_id = "synthetic-auth-session-b"
        result = self.call("shares/list")
        self.assertEqual([item["id"] for item in result["items"]], [MEMORY])
        with self.assertRaises(CommunityError):
            self.call("shares/submit", body)
        self.assertEqual(self.public_sdk.propose.call_count, 1)

    def test_message_replay_bound_to_session_and_lock_epoch(self):
        self.call("contacts/list")
        self.call("messages/send", self.send_body())
        for change in ("session", "epoch"):
            if change == "session":
                self.session_id = "synthetic-auth-session-b"
            else:
                self.session_id = "synthetic-auth-session-a"
                self.service.lock()
            self.call("contacts/list")
            with self.assertRaises(CommunityError) as caught:
                self.call("messages/send", self.send_body())
            self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(self.sdk.message_send.call_count, 1)

    def test_public_cancellation_resolves_only_new_reservations(self):
        preview = self.preview()
        reserve = self.journal.reserve
        def reserve_and_lock(identifier, fingerprint):
            result = reserve(identifier, fingerprint)
            if identifier == preview["preview_id"]:
                self.service.lock()
            return result
        self.journal.reserve = reserve_and_lock
        with self.assertRaises(CommunityError):
            self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"],
                request_id=REQUEST, consent=True))
        self.assertEqual(set(self.journal.rows), {REQUEST, preview["preview_id"]})
        self.assertTrue(all(row["result"] == {"cancelled": True} for row in self.journal.rows.values()))
        self.public_sdk.propose.assert_not_called()
        self.assertTrue(self.service.close())

    def test_cancellation_cleanup_failure_denies_clean_drain(self):
        self.call("contacts/list")
        self.sdk.pipe_resolve.side_effect = RuntimeError("definite pre-send failure")
        self.journal.complete = Mock(return_value=False)
        with self.assertRaises(CommunityError) as caught:
            self.call("messages/send", self.send_body())
        self.assertEqual(caught.exception.code, "outcome_unknown")
        self.sdk.message_send.assert_not_called()
        self.assertFalse(self.service.close())

    def test_server_request_stable_uuid_and_restart_replay_denied(self):
        self.service._messaging_readiness = lambda: "ready"
        self.service._messaging_drained = lambda: True
        self.call("contacts/list")
        self.call("messages/send", self.send_body())
        self.service = CommunityService(community_messaging=self.messaging, protected_store=self.journal,
            contact_names=("Peer",), messaging_readiness=lambda: "ready", messaging_drained=lambda: True)
        self.call("contacts/list")
        with self.assertRaises(CommunityError) as caught:
            self.call("messages/send", self.send_body())
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(self.sdk.message_send.call_count, 1)
        self.assertEqual(self.sdk.message_send.call_args.kwargs["idempotency_key"], REQUEST)

    def test_missing_or_malformed_session_denies_before_provider(self):
        for session in (None, "", "../../token", True):
            with self.subTest(session=session), self.assertRaises(CommunityError):
                self.service.dispatch("/community/status", {}, lambda: True, session_id=session)
        self.assertEqual(self.sdk.mock_calls, [])

    def test_share_answer_utf8_limit_matches_sdk_without_truncation(self):
        for answer in ("x" * 8192, "é" * 4096):
            snapshot = {**SNAPSHOT, "answer": answer}
            self.source.return_value = snapshot
            result = self.call("shares/preview", {"source_id": SOURCE, **snapshot})
            self.assertEqual(result["answer"], answer)
        self.source.reset_mock()
        for answer in ("x" * 8193, "é" * 4097):
            with self.subTest(length=len(answer)), self.assertRaises(CommunityError) as caught:
                self.call("shares/preview", {"source_id": SOURCE, **SNAPSHOT, "answer": answer})
            self.assertEqual(caught.exception.code, "share_answer_too_long")
            self.assertEqual(caught.exception.status, 400)
        self.source.assert_not_called()
        self.assertEqual(self.journal.rows, {})
        self.public_sdk.propose.assert_not_called()

    def test_durable_public_history_survives_lock_restart_and_new_session(self):
        preview = self.preview()
        self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True))
        entry = self.journal.public_rows[MEMORY]
        proposal = entry["proposal"]
        self.public_sdk.get_memory.return_value = dict(memory_id=MEMORY, submitting_agent=self.public.author,
            domain_tag=self.public.domain, memory_type="inference", classification=0,
            content=proposal.content, content_hash=proposal.content_hash, status="proposed")
        self.service.lock()
        self.assertEqual(self.service._previews, {})
        self.session_id = "new-auth-session"
        self.assertEqual(self.call("shares/list")["items"][0]["state"], "pending")
        self.service = CommunityService(community_public=self.public, protected_store=self.journal,
                                       public_readiness=lambda: "ready")
        self.assertEqual(self.call("shares/list"), {"items": [dict(id=MEMORY, question=SNAPSHOT["question"],
            state="pending", created_at="2023-11-14T22:13:20Z")], "more": False})

    def test_missing_or_failed_public_archive_never_means_empty_history(self):
        self.journal.list_public = None
        self.assertEqual(self.call("status")["sharing"], {"available": False, "reason": "not_ready"})
        with self.assertRaises(CommunityError):
            self.call("shares/list")
        self.journal.list_public = Mock(side_effect=RuntimeError("private storage diagnostic"))
        with self.assertRaises(CommunityError) as caught:
            self.call("shares/list")
        self.assertEqual(caught.exception.code, "community_unavailable")

    def test_explicit_direct_refresh_survives_restart_without_submission(self):
        self.test_durable_public_history_survives_lock_restart_and_new_session()
        before = self.public_sdk.propose.call_count
        body = dict(id=MEMORY, request_id=str(uuid.uuid4()), consent=True)
        result = self.call("shares/refresh", body)
        self.assertEqual(result["state"], "pending")
        self.assertEqual(result["trust_basis"], "unverified")
        self.assertIs(result["acceptance_available"], False)
        self.public_sdk.get_memory.return_value["content_hash"] = "e" * 64
        self.assertEqual(self.call("shares/refresh", body)["state"], "unknown")
        self.allowed = False
        reads = self.public_sdk.get_memory.call_count
        with self.assertRaises(CommunityError):
            self.call("shares/refresh", body)
        self.assertEqual(self.public_sdk.get_memory.call_count, reads)
        self.assertEqual(self.public_sdk.propose.call_count, before)

    def test_explicit_refresh_preserves_verified_direct_node_distinction(self):
        self.test_verified_online_public_states_and_revoked_trust()
        body = dict(id=MEMORY, request_id=str(uuid.uuid4()), consent=True)
        self.assertNotEqual(self.call("shares/refresh", body)["state"], "accepted")

    def test_public_archive_write_failure_keeps_submission_uncertain(self):
        preview = self.preview()
        self.journal.record_public = Mock(return_value=False)
        with self.assertRaises(CommunityError) as caught:
            self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True))
        self.assertEqual(caught.exception.code, "outcome_unknown")
        self.assertIsNone(self.journal.rows[REQUEST]["result"])
        self.assertFalse(self.service.close())

    def test_public_preparation_requires_approval_and_precedes_propose(self):
        preview = self.preview()
        self.assertEqual(self.journal.prepared, {})
        body = dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=False)
        with self.assertRaises(CommunityError):
            self.call("shares/submit", body)
        self.assertEqual(self.journal.prepared, {})
        response = self.public_sdk.propose.return_value
        def proposed(**kwargs):
            self.assertEqual(len(self.journal.prepared), 1)
            proposal, created_at = next(iter(self.journal.prepared.values()))
            self.assertEqual(proposal.content, kwargs["content"])
            self.assertEqual(created_at, "2023-11-14T22:13:20Z")
            return response
        self.public_sdk.propose.side_effect = proposed
        self.call("shares/submit", {**body, "consent": True})

    def test_failed_or_revoked_preparation_never_submits(self):
        for revoke in (False, True):
            self.setUp()
            preview = self.preview()
            def prepare(*args):
                if revoke:
                    self.allowed = False
                    return True
                return False
            self.journal.prepare_public = prepare
            with self.assertRaises(CommunityError):
                self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True))
            self.public_sdk.propose.assert_not_called()

    def test_protected_preparation_profile_failure_cancels_both_namespaces(self):
        from test_public_workflow_store import ProtectedDouble
        from sage_lantern.public_workflow_store import PublicWorkflowStore
        workflow = ProtectedDouble()
        store = PublicWorkflowStore(workflow, chain_id="public-chain", domain="commons", author="c" * 64,
                                    access_check=lambda: self.allowed, drain_check=lambda: True, identity=workflow.identity)
        public = CommunityPublic(self.public_sdk, store.sdk_journal, chain_id="public-chain", domain="commons", author="c" * 64)
        service = CommunityService(community_public=public, protected_store=store, source_fetch=self.source,
                                  public_readiness=lambda: "ready")
        preview = self.preview(service)
        self.public_sdk.get_profile.side_effect = RuntimeError("profile unavailable")
        with self.assertRaises(CommunityError):
            self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True), service)
        self.public_sdk.propose.assert_not_called()
        self.assertEqual(store.get(REQUEST)["result"], {"cancelled": True})
        self.assertTrue(store.reconcile_public(preview["preview_id"])["cancelled"])
        self.assertEqual(store.pending_count(), 0)

    def test_signed_source_status_reassignment_never_accepts_wrong_history(self):
        from test_public_workflow_store import ProtectedDouble
        from sage_lantern.public_workflow_store import PublicWorkflowStore
        workflow = ProtectedDouble()
        store = PublicWorkflowStore(workflow, chain_id="public-chain", domain="commons", author="c" * 64,
            access_check=lambda: self.allowed, drain_check=lambda: True, identity=workflow.identity)
        public = CommunityPublic(self.public_sdk, store.sdk_journal, chain_id="public-chain", domain="commons", author="c" * 64)
        public._online_verifier = Mock(return_value=OnlineNodeTrust(self.public_sdk,
            "https://community.example", "public-chain", "reviewed-revision", True, CANONICAL_DISCLOSURE))
        service = CommunityService(community_public=public, protected_store=store, source_fetch=self.source,
            public_readiness=lambda: "ready")
        preview = self.preview(service)
        self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"],
            request_id=REQUEST, consent=True), service)
        proposal = service._previews[preview["preview_id"]]["proposal"]
        self.public_sdk.get_memory.return_value = dict(memory_id=MEMORY, submitting_agent=public.author,
            domain_tag=public.domain, memory_type="inference", classification=0, content=proposal.content,
            content_hash=proposal.content_hash, status="committed")
        service.lock()
        self.assertEqual(self.call("shares/status", {"source_ids": [SOURCE]}, service),
            {"items": [{"source_id": SOURCE, "state": "accepted"}]})
        other = str(uuid.uuid4())
        row = workflow.rows[store._id("sdk", proposal.operation_id)]["payload"]
        row["source_id"] = other
        self.public_sdk.get_memory.reset_mock()
        with self.assertRaises(CommunityError):
            self.call("shares/status", {"source_ids": [other]}, service)
        self.public_sdk.get_memory.assert_not_called()

    def test_missing_source_signer_disables_readiness_before_reservation(self):
        from test_public_workflow_store import ProtectedDouble
        from sage_lantern.public_workflow_store import PublicWorkflowStore
        workflow = ProtectedDouble()
        store = PublicWorkflowStore(workflow, chain_id="public-chain", domain="commons", author="c" * 64,
            access_check=lambda: self.allowed, drain_check=lambda: True)
        public = CommunityPublic(self.public_sdk, store.sdk_journal, chain_id="public-chain", domain="commons", author="c" * 64)
        service = CommunityService(community_public=public, protected_store=store, source_fetch=self.source,
            public_readiness=lambda: "ready")
        self.assertEqual(self.call("status", service=service)["sharing"], {"available": False, "reason": "not_ready"})
        with self.assertRaises(CommunityError):
            self.call("shares/status", {"source_ids": [SOURCE]}, service)
        self.assertEqual(workflow.rows, {})

    def test_protected_preparation_unknown_dispatch_remains_pending(self):
        from test_public_workflow_store import ProtectedDouble
        from sage_lantern.public_workflow_store import PublicWorkflowStore
        workflow = ProtectedDouble()
        store = PublicWorkflowStore(workflow, chain_id="public-chain", domain="commons", author="c" * 64,
                                    access_check=lambda: self.allowed, drain_check=lambda: True, identity=workflow.identity)
        public = CommunityPublic(self.public_sdk, store.sdk_journal, chain_id="public-chain", domain="commons", author="c" * 64)
        service = CommunityService(community_public=public, protected_store=store, source_fetch=self.source,
                                  public_readiness=lambda: "ready")
        preview = self.preview(service)
        self.public_sdk.propose.side_effect = TimeoutError("unknown dispatch")
        with self.assertRaises(CommunityError) as caught:
            self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True), service)
        self.assertEqual(caught.exception.code, "outcome_unknown")
        self.assertIsNone(store.get(REQUEST)["result"])
        state = store.reconcile_public(preview["preview_id"])
        self.assertTrue(state["reserved"])
        self.assertFalse(state["cancelled"])
        self.assertEqual(store.pending_count(), 2)

    def test_protected_unknown_sdk_reservation_never_cancels_preparation(self):
        from test_public_workflow_store import ProtectedDouble
        from sage_lantern.public_workflow_store import PublicWorkflowStore
        workflow = ProtectedDouble()
        store = PublicWorkflowStore(workflow, chain_id="public-chain", domain="commons", author="c" * 64,
                                    access_check=lambda: self.allowed, drain_check=lambda: True, identity=workflow.identity)
        public = CommunityPublic(self.public_sdk, store.sdk_journal, chain_id="public-chain", domain="commons", author="c" * 64)
        service = CommunityService(community_public=public, protected_store=store, source_fetch=self.source,
                                  public_readiness=lambda: "ready")
        preview = self.preview(service)
        original_put = workflow.put
        def lose_reservation_ack(identifier, kind, revision, payload, **kwargs):
            workflow.lose_ack = payload.get("reserved") is True
            return original_put(identifier, kind, revision, payload, **kwargs)
        workflow.put = lose_reservation_ack
        with self.assertRaises(CommunityError) as caught:
            self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True), service)
        self.assertEqual(caught.exception.code, "outcome_unknown")
        self.public_sdk.propose.assert_not_called()
        self.assertIsNone(store.get(REQUEST)["result"])
        self.assertFalse(store.reconcile_public(preview["preview_id"])["cancelled"])
        self.assertEqual(store.pending_count(), 2)

    def test_public_archive_binding_and_count_validated_before_poll(self):
        preview = self.preview()
        self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True))
        entry = self.journal.public_rows[MEMORY]
        invalid_rows = [
            [{**entry, "proposal": replace(entry["proposal"], author="e" * 64)}],
            [{**entry, "receipt": replace(entry["receipt"], intended_chain_id="other-chain")}],
            [{**entry, "created_at": "not-a-date"}],
            [entry] * 22,
        ]
        for rows in invalid_rows:
            self.journal.list_public = Mock(return_value=rows)
            with self.assertRaises(CommunityError):
                self.call("shares/list")
        self.public_sdk.get_memory.assert_not_called()

    def test_public_archive_page_is_bounded_to_twenty(self):
        preview = self.preview()
        self.call("shares/submit", dict(preview_id=preview["preview_id"], digest=preview["digest"], request_id=REQUEST, consent=True))
        entry = self.journal.public_rows[MEMORY]
        rows = [{**entry, "receipt": replace(entry["receipt"], memory_id=str(uuid.uuid4()))} for _ in range(21)]
        self.journal.list_public = Mock(return_value=rows)
        result = self.call("shares/list")
        self.assertEqual(len(result["items"]), 20)
        self.assertTrue(result["more"])
        self.journal.list_public.assert_called_once_with(limit=21)
        self.assertEqual(self.public_sdk.get_memory.call_count, 20)

    def test_dictation_constructor_requires_literal_true_and_access_guard(self):
        for value in (None, False, 1, "true", True):
            service = CommunityService(dictation_available=lambda: value)
            self.assertEqual(self.call("status", service=service)["dictation"], {"available": value is True})
        def revoked():
            self.allowed = False
            return True
        with self.assertRaises(CommunityError) as caught:
            self.call("status", service=CommunityService(dictation_available=revoked))
        self.assertEqual(caught.exception.code, "device_locked")


if __name__ == "__main__":
    unittest.main()
