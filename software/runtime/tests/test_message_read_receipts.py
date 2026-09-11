import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_read_receipts import (
    QueueOutcome, ReadProjection, ReadReceiptAdapter, ReceiptBinding,
)


class ReadReceiptTests(unittest.TestCase):
    def setUp(self):
        self.sender = RecipientID("sender-chain", "a" * 64)
        self.recipient = RecipientID("recipient-chain", "b" * 64)
        self.binding = ReceiptBinding(self.sender, self.recipient, "msg-outgoing", "fed-incoming",
                                      "original-event", "c" * 64, "epoch", "agreement", "contact", "revision")
        self.challenge = {"pipe_id": "fed-incoming", "event_kind": "read",
                          "challenge": self.binding.challenge_body()}
        self.client = Mock()
        self.client.pipe_receipt_challenge.return_value = self.challenge
        self.client.pipe_receipt_record.return_value = {
            "pipe_id": "fed-incoming", "event_kind": "read",
            "receipt_status": "queued", "idempotent_replay": False,
        }
        self.client.pipe_receipt_status.return_value = {
            "pipe_id": "msg-outgoing", "protocol": "receipt-v2", "transport_status": "delivered",
            "claim_status": "unconfirmed", "read_status": "confirmed", "read_at": "2026-09-05T00:00:00Z",
        }

    def adapter(self, **options):
        return ReadReceiptAdapter(client=self.client, actor=options.pop("actor", self.recipient),
                                  guard=options.pop("guard", lambda binding, session: True), **options)

    def acknowledge(self, adapter, **options):
        arguments = dict(open_id="open-1", session_id="session-1", explicit_open=True,
                         privacy_enabled=True, receipt_protocol_version=2)
        arguments.update(options)
        return adapter.acknowledge_open(self.binding, **arguments)

    def test_passes_complete_challenge_to_sdk_and_returns_only_queued(self):
        original = deepcopy(self.challenge)
        self.assertIs(self.acknowledge(self.adapter()), QueueOutcome.QUEUED)
        self.client.pipe_receipt_challenge.assert_called_once_with("fed-incoming", "read")
        self.client.pipe_receipt_record.assert_called_once_with("fed-incoming", "read", original)
        self.assertEqual(self.challenge, original)
        self.client.pipe_receipt_status.assert_not_called()

    def test_disabled_poll_and_unsupported_have_no_calls(self):
        for options in ({"explicit_open": False}, {"explicit_open": 1},
                        {"privacy_enabled": False}, {"privacy_enabled": "true"},
                        {"receipt_protocol_version": True}, {"receipt_protocol_version": 1}):
            self.assertIn(self.acknowledge(self.adapter(), **options),
                          (QueueOutcome.NOT_REQUESTED, QueueOutcome.UNSUPPORTED))
        self.assertEqual(self.client.mock_calls, [])

    def test_missing_false_or_raising_guard_denies(self):
        for guard in (None, lambda *args: False, lambda *args: 1, Mock(side_effect=RuntimeError("secret"))):
            self.assertIs(self.acknowledge(self.adapter(guard=guard)), QueueOutcome.CANCELLED)
        self.assertEqual(self.client.mock_calls, [])

    def test_guard_cancellation_before_challenge(self):
        guard = Mock(side_effect=[True, False])
        self.assertIs(self.acknowledge(self.adapter(guard=guard)), QueueOutcome.CANCELLED)
        self.assertEqual(self.client.mock_calls, [])

    def test_guard_cancellation_between_challenge_and_mutation(self):
        guard = Mock(side_effect=[True, True, False])
        self.assertIs(self.acknowledge(self.adapter(guard=guard)), QueueOutcome.CANCELLED)
        self.client.pipe_receipt_record.assert_not_called()

    def test_cancellation_after_dispatch_is_unknown(self):
        guard = Mock(side_effect=[True, True, True, False])
        self.assertIs(self.acknowledge(self.adapter(guard=guard)), QueueOutcome.UNKNOWN)
        self.client.pipe_receipt_record.assert_called_once()

    def test_wrong_actor_cannot_ack_or_inspect_sender(self):
        self.assertIs(self.acknowledge(self.adapter(actor=self.sender)), QueueOutcome.UNAVAILABLE)
        self.assertIs(self.adapter().sender_status(self.binding, session_id="session"), ReadProjection.UNKNOWN)
        self.assertEqual(self.client.mock_calls, [])

    def test_every_inner_binding_and_outer_pipe_must_match(self):
        for field in self.challenge["challenge"]:
            altered = deepcopy(self.challenge)
            altered["challenge"][field] = "wrong"
            self.client.pipe_receipt_challenge.return_value = altered
            self.assertIs(self.acknowledge(self.adapter()), QueueOutcome.UNAVAILABLE, field)
        for field in ("pipe_id", "event_kind"):
            altered = deepcopy(self.challenge)
            altered[field] = "wrong"
            self.client.pipe_receipt_challenge.return_value = altered
            self.assertIs(self.acknowledge(self.adapter()), QueueOutcome.UNAVAILABLE)
        self.client.pipe_receipt_record.assert_not_called()

    def test_no_claim_or_unknown_field_signed(self):
        self.client.pipe_receipt_challenge.return_value["challenge"]["unexpected"] = "content"
        self.assertIs(self.acknowledge(self.adapter()), QueueOutcome.UNAVAILABLE)
        self.client.pipe_receipt_record.assert_not_called()

    def test_optional_linked_fields_preserved(self):
        self.binding = replace(self.binding, authorization_mode="linked-v23", relation_digest="d" * 64)
        self.challenge["challenge"] = self.binding.challenge_body()
        self.assertIs(self.acknowledge(self.adapter()), QueueOutcome.QUEUED)
        self.assertEqual(self.client.pipe_receipt_record.call_args.args[2], self.challenge)

    def test_exceptions_and_bad_queue_results_never_mean_read(self):
        self.client.pipe_receipt_challenge.side_effect = RuntimeError("private")
        self.assertIs(self.acknowledge(self.adapter()), QueueOutcome.UNAVAILABLE)
        self.client.pipe_receipt_challenge.side_effect = None
        self.client.pipe_receipt_record.side_effect = RuntimeError("private")
        self.assertIs(self.acknowledge(self.adapter()), QueueOutcome.UNKNOWN)
        self.client.pipe_receipt_record.side_effect = None
        self.client.pipe_receipt_record.return_value = {"read_status": "confirmed"}
        self.assertIs(self.acknowledge(self.adapter()), QueueOutcome.UNKNOWN)

    def test_open_replay_and_capacity_do_not_repeat_mutations(self):
        adapter = self.adapter(max_opens=1)
        self.assertIs(self.acknowledge(adapter), QueueOutcome.QUEUED)
        self.assertIs(self.acknowledge(adapter), QueueOutcome.UNAVAILABLE)
        self.assertIs(self.acknowledge(adapter, open_id="open-2"), QueueOutcome.UNAVAILABLE)
        self.client.pipe_receipt_record.assert_called_once()

    def test_sender_poll_is_read_only_and_requires_confirmed_projection(self):
        adapter = self.adapter(actor=self.sender)
        self.assertIs(adapter.sender_status(self.binding, session_id="session"), ReadProjection.CONFIRMED)
        self.client.pipe_receipt_status.assert_called_once_with("msg-outgoing")
        self.client.pipe_receipt_challenge.assert_not_called()
        self.client.pipe_receipt_record.assert_not_called()
        for field, value in (("pipe_id", "wrong"), ("protocol", "legacy"),
                             ("read_status", "unconfirmed"), ("read_at", None)):
            original = self.client.pipe_receipt_status.return_value.copy()
            self.client.pipe_receipt_status.return_value[field] = value
            self.assertIs(adapter.sender_status(self.binding, session_id="session"), ReadProjection.UNKNOWN)
            self.client.pipe_receipt_status.return_value = original

    def test_unsupported_or_failed_status_has_no_read_fallback(self):
        adapter = self.adapter(actor=self.sender)
        self.client.pipe_receipt_status.return_value["protocol"] = "unsupported"
        self.assertIs(adapter.sender_status(self.binding, session_id="session"), ReadProjection.UNSUPPORTED)
        self.client.pipe_receipt_status.side_effect = RuntimeError("private")
        self.assertIs(adapter.sender_status(self.binding, session_id="session"), ReadProjection.UNKNOWN)
        self.client.pipe_receipt_record.assert_not_called()

    def test_strict_limits(self):
        for options in ({"open_id": ""}, {"session_id": "x" * 257}):
            self.assertIs(self.acknowledge(self.adapter(), **options), QueueOutcome.UNAVAILABLE)
        with self.assertRaises(ValueError):
            self.adapter(max_opens=True)
        with self.assertRaises(ValueError):
            replace(self.binding, content_digest="signature-shaped")


if __name__ == "__main__":
    unittest.main()
