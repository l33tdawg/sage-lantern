import unittest
from dataclasses import replace
from unittest.mock import Mock

from sage_lantern.community_messaging import CommunityMessaging, MessagingUnavailable, Prerequisites
from sage_lantern.message_contacts import ContactClaim, RecipientID


class SDKModel:
    def __init__(self, value):
        self.value = value

    def model_dump(self, *, mode):
        assert mode == "json"
        return self.value


class CommunityMessagingTests(unittest.TestCase):
    def setUp(self):
        self.actor = RecipientID("home", "a" * 64)
        self.recipient = RecipientID("home", "b" * 64)
        self.client = Mock()
        self.state = Prerequisites(self.actor, True, True, True, True, True, "operator", "policy-1")
        self.guard = Mock(return_value=True)
        self.claim = ContactClaim(self.actor, self.recipient, "A label", "operator", "policy-1", 1,
                                  100, 160, True, True)
        self.verifier = Mock(side_effect=lambda *args: self.claim)
        self.adapter = CommunityMessaging(self.client, actor=self.actor, prerequisites=lambda: self.state,
                                          guard=self.guard, verify_recipient=self.verifier, clock=lambda: 110)
        self.row = dict(pipe_id="message-1", from_agent=self.recipient.agent_id,
                        to_agent=self.actor.agent_id, payload="untrusted message", status="pending",
                        created_at="2026-09-06T12:00:00Z")
        self.client.pipe_inbox_history.return_value = SDKModel(dict(items=[self.row], count=1))
        self.client.pipe_outbox.return_value = SDKModel(dict(items=[dict(self.row,
            from_agent=self.actor.agent_id, to_agent=self.recipient.agent_id)], count=1))
        self.client.lookup_agents.return_value = SDKModel(dict(agents=[dict(agent_id=self.recipient.agent_id,
            name="A label", registered_name="label", status="active", match_kind="exact")], total=1))
        self.client.pipe_resolve.return_value = SDKModel(dict(to_agent=self.recipient.agent_id))
        self.client.message_send.return_value = SDKModel(dict(message_id="message-2", status="pending",
            idempotent_replay=False, expires_at="2026-09-06T12:00:00Z"))

    def send(self, **changes):
        fields = dict(recipient=self.recipient, payload="approved text", idempotency_key="request-1",
                      ttl_minutes=60, approved=True)
        fields.update(changes)
        return self.adapter.send("session", **fields)

    def test_probed_storage_requires_server_encrypted_admission(self):
        self.state = replace(self.state, storage_epoch='c' * 64 + ':2')
        self.assertEqual(self.send().outcome, 'accepted_by_node')
        self.assertIs(self.client.message_send.call_args.kwargs['require_encrypted_storage'], True)

    def test_storage_epoch_change_during_send_is_unknown(self):
        self.state = replace(self.state, storage_epoch='c' * 64 + ':2')
        response = self.client.message_send.return_value
        def change_epoch(**kwargs):
            self.state = replace(self.state, storage_epoch='c' * 64 + ':3')
            return response
        self.client.message_send.side_effect = change_epoch
        self.assertEqual(self.send().outcome, 'unknown')

    def test_disabled_by_default_no_sdk_calls(self):
        adapter = CommunityMessaging(self.client, actor=self.actor)
        self.assertFalse(any(adapter.capabilities("session").values()))
        with self.assertRaises(MessagingUnavailable):
            adapter.inbox_history("session")
        self.client.assert_not_called()
        self.assertEqual(self.client.method_calls, [])

    def test_plaintext_unverified_identity_and_nonbool_security_deny(self):
        for changes in (dict(encrypted_storage_verified=False), dict(authenticated_node_and_signer=1),
                        dict(actor=self.recipient)):
            self.state = replace(self.state, **changes)
            with self.assertRaises(MessagingUnavailable):
                self.adapter.inbox_history("session")
        self.assertEqual(self.client.method_calls, [])

    def test_passive_history_only_and_untrusted_projection(self):
        inbox = self.adapter.inbox_history("session", limit=5)
        outbox = self.adapter.outbox("session", limit=5)
        self.assertEqual(inbox[0].sender, self.recipient)
        self.assertEqual(outbox[0].sender, self.actor)
        self.assertEqual(inbox[0].trust, "untrusted_data")
        self.assertEqual([call[0] for call in self.client.method_calls], ["pipe_inbox_history", "pipe_outbox"])
        self.client.pipe_inbox_history.assert_called_once_with(limit=5)

    def test_history_omits_foreign_provider_and_other_participant_rows(self):
        rows = [dict(self.row, source_chain_id="remote"), dict(self.row, to_provider="shared"),
                dict(self.row, to_agent=self.recipient.agent_id)]
        self.client.pipe_inbox_history.return_value = dict(items=rows, count=3)
        self.assertEqual(self.adapter.inbox_history("session"), ())

    def test_history_preserves_source_creation_time_and_limit_21(self):
        for timestamp in ("2026-09-06T12:00:00Z", "2026-09-06T12:00:00.123456789Z",
                          "2026-09-06T20:00:00+08:00"):
            with self.subTest(timestamp=timestamp):
                self.row["created_at"] = timestamp
                self.assertEqual(self.adapter.inbox_history("session", limit=21)[0].created_at, timestamp)
                self.client.pipe_inbox_history.assert_called_with(limit=21)
        self.assertEqual(self.adapter.outbox("session", limit=21)[0].created_at, "2026-09-06T12:00:00Z")
        self.client.pipe_outbox.assert_called_with(limit=21)

    def test_history_missing_or_invalid_creation_time_never_fabricated(self):
        for timestamp in (None, True, 123, "", "2026-09-06", "2026-09-06T12:00:00",
                          "2026-02-30T12:00:00Z", "2026-09-06T25:00:00Z", "2026-09-06T12:00:00+08:99"):
            with self.subTest(timestamp=timestamp):
                self.row["created_at"] = timestamp
                with self.assertRaises(MessagingUnavailable):
                    self.adapter.inbox_history("session")
        del self.row["created_at"]
        with self.assertRaises(MessagingUnavailable):
            self.adapter.inbox_history("session")

    def test_history_rechecks_guard_and_does_not_leak_content(self):
        self.guard.side_effect = [True, False]
        with self.assertRaisesRegex(MessagingUnavailable, "^messaging_unavailable$"):
            self.adapter.inbox_history("session")

    def test_bad_history_and_limits_fail_closed(self):
        for value in (dict(items=[self.row], count=True), dict(items=[self.row, self.row], count=2),
                      dict(items=[dict(self.row, payload="x" * ((256 << 10) + 1))], count=1)):
            self.client.pipe_inbox_history.return_value = value
            with self.assertRaises(MessagingUnavailable):
                self.adapter.inbox_history("session")
        for limit in (True, 0, 101, 1.5):
            with self.assertRaises(MessagingUnavailable):
                self.adapter.outbox("session", limit=limit)

    def test_directory_verified_exact_identity_not_label(self):
        self.assertEqual(self.adapter.directory("session", name="A label"), (self.claim,))
        self.client.lookup_agents.assert_called_once_with(name="A label", limit=20)
        self.assertEqual(self.verifier.call_args.args[:2], (self.actor, self.recipient))

    def test_no_verifier_or_unverified_proof_shape_no_contacts(self):
        self.adapter._verify = None
        with self.assertRaises(MessagingUnavailable):
            self.adapter.directory("session", name="label")
        self.client.lookup_agents.assert_not_called()
        self.adapter._verify = Mock(return_value=dict(signature="shape"))
        with self.assertRaises(MessagingUnavailable):
            self.adapter.directory("session", name="label")

    def test_stale_revoked_wrong_principal_policy_and_nonfindable_deny(self):
        for changes in (dict(expires_at=105), dict(issued_at=120, expires_at=180), dict(caller=self.recipient),
                        dict(recipient=self.actor), dict(policy_binding="old"), dict(issuer="other"),
                        dict(findable=False), dict(messaging_allowed=False)):
            self.claim = replace(ContactClaim(self.actor, self.recipient, "label", "operator", "policy-1",
                                             1, 100, 160, True, True), **changes)
            with self.assertRaises(MessagingUnavailable):
                self.send()
        self.client.message_send.assert_not_called()

    def test_send_exact_sdk_fields_and_bound_guard(self):
        result = self.send()
        self.assertEqual(result.outcome, "accepted_by_node")
        self.client.pipe_resolve.assert_called_once_with(to=self.recipient.agent_id)
        self.client.message_send.assert_called_once_with(to_agent=self.recipient.agent_id,
            payload="approved text", idempotency_key="request-1", ttl_minutes=60)
        request = self.guard.call_args.args[0]
        self.assertEqual(request.recipient, self.recipient)
        self.assertEqual(request.idempotency_key, "request-1")
        self.assertEqual(len(request.payload_digest), 64)
        self.assertEqual(request.ttl_minutes, 60)

    def test_approval_ttl_and_cross_chain_rejected_before_sdk(self):
        for changes in (dict(approved=False), dict(approved=1), dict(ttl_minutes=0), dict(ttl_minutes=True),
                        dict(ttl_minutes=1441), dict(recipient=RecipientID("remote", "b" * 64)),
                        dict(idempotency_key=" "), dict(payload="\ud800")):
            with self.assertRaises(MessagingUnavailable):
                self.send(**changes)
        self.assertEqual(self.client.method_calls, [])

    def test_resolve_rebinding_and_guard_cancellation_prevent_send(self):
        self.client.pipe_resolve.return_value = dict(to_agent=self.actor.agent_id)
        with self.assertRaises(MessagingUnavailable):
            self.send()
        self.client.pipe_resolve.return_value = dict(to_agent=self.recipient.agent_id)
        self.guard.side_effect = [True, False]
        with self.assertRaises(MessagingUnavailable):
            self.send()
        self.client.message_send.assert_not_called()

    def test_post_dispatch_failure_is_unknown_no_automatic_retry(self):
        self.client.message_send.side_effect = RuntimeError("private payload and key")
        result = self.send()
        self.assertEqual(result.outcome, "unknown")
        self.assertIsNone(result.message_id)
        self.client.message_send.assert_called_once()

    def test_post_dispatch_guard_and_malformed_receipt_are_unknown(self):
        self.guard.side_effect = [True, True, True, False]
        self.assertEqual(self.send().outcome, "unknown")
        self.guard.side_effect = None
        self.client.message_send.return_value = dict(message_id="m", status="read", idempotent_replay=False)
        self.assertEqual(self.send().outcome, "unknown")

    def test_capability_limits_and_no_disk_network_configuration(self):
        value = self.adapter.capabilities("session")
        self.assertTrue(value["send"])
        for name in ("radio", "federated_send", "durable_send", "end_to_end_encrypted"):
            self.assertFalse(value[name])
        self.assertEqual(self.client.method_calls, [])


if __name__ == "__main__":
    unittest.main()
