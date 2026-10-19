"""Token-engine request-shape tests; no engine or device calls."""
from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "software/npu/rkllm_server.py"
SPEC = importlib.util.spec_from_file_location("rkllm_token_protocol_test", SOURCE)
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class FakeEncoding:
    ids = [151644, 9707, 151645]


class FakeTokenizer:
    def encode(self, prompt, add_special_tokens=False):
        assert add_special_tokens is False
        return FakeEncoding()


def engine(input_type="TOKEN"):
    value = SERVER.Engine.__new__(SERVER.Engine)
    value.input_type = input_type
    value.tokenizer = FakeTokenizer()
    value.context = 2048
    return value


class TokenProtocolTests(unittest.TestCase):
    def test_token_request_has_strict_field_order_and_both_inputs(self):
        line = engine()._request("Hello", 320, True)
        match = re.fullmatch(
            r'\{"id":"([0-9a-f]{12})","prompt_utf8_hex":"([0-9a-f]+)",'
            r'"input_ids":\[151644,9707,151645\],"max_new_tokens":320,"stream":true\}',
            line)
        self.assertIsNotNone(match)
        self.assertEqual(bytes.fromhex(match.group(2)), b"Hello")

    def test_prompt_engine_shape_is_unchanged(self):
        line = engine("PROMPT")._request("Hello", 64, False)
        self.assertEqual(json.loads(line)["prompt"], "Hello")
        self.assertIs(json.loads(line)["stream"], False)

    def test_context_overflow_refuses(self):
        value = engine()
        value.context = 4
        with self.assertRaisesRegex(SERVER.EngineError, "context"):
            value._request("Hello", 320, False)


if __name__ == "__main__":
    unittest.main()
