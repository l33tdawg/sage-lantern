"""Assistant-prefix selection tests for the RKLLM chat renderer."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "software/npu/rkllm_server.py"
SPEC = importlib.util.spec_from_file_location("rkllm_prefix_test", SOURCE)
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class AssistantPrefixTests(unittest.TestCase):
    def test_plain_prefix_has_no_think_block(self):
        rendered = SERVER.render_chat(
            [{"role": "user", "content": "hi"}],
            assistant_prefix=SERVER.PLAIN_ASSISTANT_PREFIX)
        self.assertTrue(rendered.endswith("<|im_start|>assistant\n"))
        self.assertNotIn("<think>", rendered)

    def test_think_prefix_is_default(self):
        rendered = SERVER.render_chat([{"role": "user", "content": "hi"}])
        self.assertTrue(rendered.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n"))


if __name__ == "__main__":
    unittest.main()
