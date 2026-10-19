import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]/'lantern/benchmarks/tool_routing'
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location('specialist_latency', ROOT/'bench_latency.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PrefixTests(unittest.TestCase):
    def test_prefix_build_contains_system_policy_only(self):
        class Tokenizer:
            def apply_chat_template(self, messages, **kwargs):
                self.messages, self.options = messages, kwargs
                return [1, 2, 3]
        tok = Tokenizer()
        self.assertEqual(MODULE.static_tokens(tok, False), [1,2,3])
        self.assertEqual(len(tok.messages), 1)
        self.assertEqual(tok.messages[0]['role'], 'system')
        self.assertNotIn('REQUEST:', tok.messages[0]['content'])
        self.assertIs(tok.options['add_generation_prompt'], False)

    def test_question_is_only_in_disposable_suffix(self):
        prefix = [1,2,3]
        self.assertEqual(MODULE.suffix([1,2,3,4,5], prefix), [4,5])
        self.assertEqual(prefix, [1,2,3])

    def test_empty_mismatch_and_no_suffix_rejected(self):
        for tokens,prefix in [([1,2],[]),([1,2],[9]),([1,2],[1,2]),([1],[1,2])]:
            with self.subTest(tokens=tokens,prefix=prefix):
                with self.assertRaisesRegex(ValueError, 'prefix_mismatch'):
                    MODULE.suffix(tokens, prefix)


if __name__ == '__main__':
    unittest.main()
