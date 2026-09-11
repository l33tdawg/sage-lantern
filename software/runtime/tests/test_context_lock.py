import io
import json
import unittest
from unittest.mock import patch

from sage_lantern.adapters import LanternConversationContext


class ConversationContextLockTests(unittest.TestCase):
    def test_forwards_captured_environment_token_only_in_header(self):
        with patch.dict("os.environ", {"LANTERN_DEVICE_TOKEN": "synthetic-token"}), patch(
            "urllib.request.urlopen", return_value=io.BytesIO(b'{"context":["answer"]}')
        ) as opened:
            self.assertEqual(LanternConversationContext().recall("question"), ["answer"])
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("X-lantern-session"), "synthetic-token")
        self.assertEqual(json.loads(request.data), {"query": "question"})
        self.assertNotIn("synthetic-token", request.full_url)

    def test_legacy_without_token_omits_header(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "urllib.request.urlopen", return_value=io.BytesIO(b'{"context":[]}')
        ) as opened:
            self.assertEqual(LanternConversationContext().recall("question"), [])
        self.assertIsNone(opened.call_args.args[0].get_header("X-lantern-session"))

    def test_header_injection_and_oversize_fail_before_network(self):
        for token in ("secret\r\nInjected: yes", "secret\x00", "x" * 4097, "non-ascii-☃"):
            with patch("sage_lantern.adapters.os.environ", {"LANTERN_DEVICE_TOKEN": token}), patch("urllib.request.urlopen") as opened:
                with self.assertRaisesRegex(RuntimeError, "^Conversation memory unavailable$"):
                    LanternConversationContext().recall("question")
                opened.assert_not_called()

    def test_denied_request_never_retries_without_token(self):
        with patch.dict("os.environ", {"LANTERN_DEVICE_TOKEN": "old-token"}), patch(
            "urllib.request.urlopen", side_effect=PermissionError("denied")
        ) as opened:
            with self.assertRaises(PermissionError):
                LanternConversationContext().recall("question")
        opened.assert_called_once()
