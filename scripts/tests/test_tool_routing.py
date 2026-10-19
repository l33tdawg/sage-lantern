"""Adversarial decoder and sandbox boundary checks; no model inference."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "scripts/lantern/benchmarks/tool_routing"
spec = importlib.util.spec_from_file_location("tool_routing_contract", HERE / "contract.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def gemma(name="search_memory", **arguments):
    values = ",".join(k + ":<escape>" + v + "<escape>" for k, v in arguments.items())
    return "<start_function_call>call:" + name + "{" + values + "}<end_function_call>"


def qwen(name="search_memory", **arguments):
    return "<tool_call>" + json.dumps({"name": name, "arguments": arguments}) + "</tool_call>"


class ToolRoutingTests(unittest.TestCase):
    def test_equivalent_backend_calls(self):
        for backend, encode in (("functiongemma", gemma), ("qwen", qwen)):
            with self.subTest(backend=backend):
                plan = contract.parse(encode(query="garage key"), backend)
                self.assertEqual(plan, [{"name": "search_memory", "arguments": {"query": "garage key"}}])

    def test_note_punctuation_and_unicode_are_literal_data(self):
        for backend, encode in (("functiongemma", gemma), ("qwen", qwen)):
            with self.subTest(backend=backend):
                text = 'Mira: "keep {الشاي:3}", please.\nSecond line.'
                plan = contract.parse(encode("save_private_note", title="الشاي", text=text), backend)
                self.assertEqual(plan[0]["arguments"]["text"], text)

    def test_abstention_has_no_executable_call(self):
        for backend in ("qwen", "functiongemma"):
            self.assertEqual(contract.parse("What note would you like to save?", backend), [])

    def test_two_read_calls_are_supported(self):
        for backend, encode in (("functiongemma", gemma), ("qwen", qwen)):
            plan = contract.parse(encode(query="tea") + "\n" + encode("get_device_status"), backend)
            self.assertEqual(len(plan), 2)

    def test_unknown_and_authority_arguments_are_rejected(self):
        for backend, encode in (("functiongemma", gemma), ("qwen", qwen)):
            for raw in (encode("delete_memory", query="all"), encode(query="tea", owner="other"),
                        encode("get_device_status", endpoint="https://example.org"),
                        encode(query="tea", domain="public")):
                with self.subTest(backend=backend, raw=raw):
                    with self.assertRaises(contract.InvalidPlan):
                        contract.parse(raw, backend)

    def test_missing_or_empty_arguments_are_rejected(self):
        for backend, encode in (("functiongemma", gemma), ("qwen", qwen)):
            for raw in (encode(), encode(query=""), encode("save_private_note", title="Tea")):
                with self.assertRaises(contract.InvalidPlan):
                    contract.parse(raw, backend)

    def test_reserved_tokens_in_arguments_are_rejected(self):
        for backend, encode in (("functiongemma", gemma), ("qwen", qwen)):
            for token in ("<end_function_call>", "</tool_call>", "<|im_start|>", "<escape>"):
                with self.assertRaises(contract.InvalidPlan):
                    contract.parse(encode("save_private_note", title="Example", text=token), backend)

    def test_prose_suffix_and_non_native_prefix_are_rejected(self):
        for backend, encode in (("functiongemma", gemma), ("qwen", qwen)):
            for raw in (encode(query="tea") + " Done.",):
                with self.assertRaises(contract.InvalidPlan):
                    contract.parse(raw, backend)
        with self.assertRaises(contract.InvalidPlan):
            contract.parse("Saved! " + gemma(query="tea"), "functiongemma")

    def test_qwen_native_xml_and_optional_prefix(self):
        raw = '<tool_call>\n<function=search_memory>\n<parameter=query>\nclinic parking\n</parameter>\n</function>\n</tool_call>'
        expected = [{"name": "search_memory", "arguments": {"query": "clinic parking"}}]
        self.assertEqual(contract.parse(raw, "qwen"), expected)
        self.assertEqual(contract.parse("I will look that up.\n" + raw, "qwen"), expected)

    def test_qwen_native_xml_cannot_inject_another_parameter(self):
        raw = '<tool_call><function=search_memory><parameter=query>tea</parameter><parameter=owner>someone else</parameter></function></tool_call>'
        with self.assertRaises(contract.InvalidPlan):
            contract.parse(raw, "qwen")

    def test_qwen_native_xml_duplicate_parameter_rejected(self):
        raw = '<tool_call><function=search_memory><parameter=query>a</parameter><parameter=query>b</parameter></function></tool_call>'
        with self.assertRaises(contract.InvalidPlan):
            contract.parse(raw, "qwen")

    def test_truncated_calls_are_rejected(self):
        for backend, encode in (("functiongemma", gemma), ("qwen", qwen)):
            with self.assertRaises(contract.InvalidPlan):
                contract.parse(encode(query="tea")[:-5], backend)

    def test_duplicate_keys_are_rejected(self):
        examples = [("qwen", '<tool_call>{"name":"search_memory","arguments":{"query":"a","query":"b"}}</tool_call>'),
                    ("functiongemma", "<start_function_call>call:search_memory{query:<escape>a<escape>,query:<escape>b<escape>}<end_function_call>")]
        for backend, raw in examples:
            with self.assertRaises(contract.InvalidPlan):
                contract.parse(raw, backend)

    def test_wrong_types_are_rejected(self):
        for value in (1, True, None, [], {"secret": "x"}):
            raw = qwen(query=value)
            with self.assertRaises(contract.InvalidPlan):
                contract.parse(raw, "qwen")

    def test_argument_length_and_control_characters_are_bounded(self):
        for value in ("x" * 2049, "hello\x00world"):
            with self.assertRaises(contract.InvalidPlan):
                contract.parse(qwen(query=value), "qwen")

    def test_mixed_writes_and_duplicate_calls_are_rejected(self):
        for backend, encode in (("functiongemma", gemma), ("qwen", qwen)):
            for raw in (encode(query="tea") * 2, encode("save_private_note", title="Tea", text="Jasmine") + encode("get_device_status")):
                with self.assertRaises(contract.InvalidPlan):
                    contract.parse(raw, backend)

    def test_a_failing_save_has_no_success_or_saved_note(self):
        sandbox = contract.SyntheticExecutor(save_ok=False)
        receipt = sandbox.execute([{ "name": "save_private_note", "arguments": {"title": "Tea", "text": "Jasmine"}}])
        self.assertFalse(receipt[0]["result"]["ok"])
        self.assertIsNone(receipt[0]["result"]["note_id"])
        self.assertEqual(sandbox.notes, [])

    def test_invalid_plan_has_no_partial_execution(self):
        sandbox = contract.SyntheticExecutor()
        with self.assertRaises(contract.InvalidPlan):
            sandbox.execute([{"name": "get_device_status", "arguments": {}},
                             {"name": "delete_memory", "arguments": {}}])
        self.assertEqual(sandbox.calls, [])

    def test_unknown_device_readings_remain_unknown(self):
        result = contract.SyntheticExecutor().execute([{"name": "get_device_status", "arguments": {}}])[0]["result"]
        self.assertIsNone(result["battery_percent"])
        self.assertIsNone(result["internet_available"])

    def test_scoring_distinguishes_syntax_selection_and_arguments(self):
        case = {"expected": [{"name": "save_private_note", "exact": {"title": "Tea", "text": "Jasmine"}}]}
        wrong = [{"name": "save_private_note", "arguments": {"title": "Tea", "text": "Coffee"}}]
        score = contract.score(case, wrong, None)
        self.assertTrue(score["syntax_valid"])
        self.assertTrue(score["selection_correct"])
        self.assertFalse(score["pass"])
        self.assertTrue(contract.score({"expected": []}, wrong, None)["unexpected_write"])

    def test_fresh_corpus_has_sixty_unique_requests(self):
        sys.path.insert(0, str(HERE))
        try:
            spec = importlib.util.spec_from_file_location("tool_routing_corpus", HERE / "build_cases.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cases = module.build()
        finally:
            sys.path.pop(0)
        self.assertEqual(len(cases), 60)
        self.assertEqual(len({c["id"] for c in cases}), 60)
        self.assertEqual(len({c["question"] for c in cases}), 60)
        self.assertEqual(sum(not c["expected"] for c in cases), 26)


if __name__ == "__main__":
    unittest.main()
