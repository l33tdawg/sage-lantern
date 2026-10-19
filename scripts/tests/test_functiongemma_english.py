"""Evidence and backend boundaries for the English action challenge."""
import copy
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1] / "lantern/benchmarks/tool_routing"
sys.path.insert(0, str(HERE))
from build_english_qualification import canonical, rows as fixtures
from english_qualification import fit_policy, fingerprint, require_backend, validate_outputs, summarize
from intent_gate import INTENTS
from run_gate import probabilities


def prediction(i, intent="search", correct=True):
    raw = {k: 0.0 for k in INTENTS}
    raw[intent] = 30.0
    orders = [{"reverse": reverse, "logits": raw.copy(), "prompt_tokens": 300, "label_mass": 1.0}
              for reverse in (False, True)]
    return {"id": f"calibration-{i}", "question": f"Find my note on topic {i}.", "orders": orders,
            "probabilities": probabilities(orders), "order_agrees": True,
            "expected_intent": intent if correct else "answer", "label_mass": 1.0,
            "milliseconds": 10.0}


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.backend = {"artifacts": {"bundle": {"classifier.safetensors": "same-adapter"}},
                        "tokenizer": "template-a", "execution": "full_compiled", "packages": {"mlx": "locked"}}

    def test_same_adapter_different_tokenizer_is_not_calibrated(self):
        policy = {"backend": self.backend, "backend_sha256": fingerprint(self.backend)}
        require_backend(policy, self.backend)
        changed = {**self.backend, "tokenizer": "template-b"}
        with self.assertRaisesRegex(ValueError, "backend_mismatch"):
            require_backend(policy, changed)

    def test_batching_or_runtime_change_requires_separate_calibration(self):
        policy = {"backend": self.backend, "backend_sha256": fingerprint(self.backend)}
        for key, value in (("execution", "full_batch2"), ("packages", {"mlx": "new"})):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "backend_mismatch"):
                require_backend(policy, {**self.backend, key: value})

    def test_hash_only_policy_without_full_binding_is_refused(self):
        with self.assertRaisesRegex(ValueError, "backend_mismatch"):
            require_backend({"backend_sha256": fingerprint(self.backend)}, self.backend)

    def test_confirmation_cannot_fit_its_own_thresholds(self):
        r = prediction(1)
        r["id"] = "confirmation-1"
        with self.assertRaisesRegex(ValueError, "calibration_only"):
            fit_policy([r], self.backend, "test-scores")

    def test_zero_or_small_support_disables_an_intent(self):
        policy = fit_policy([prediction(i) for i in range(4)], self.backend, "test-scores")
        self.assertGreater(policy["thresholds"]["search"], 1)
        self.assertTrue(policy["calibration_support"]["search"]["disabled"])
        self.assertGreater(policy["thresholds"]["save"], 1)

    def test_confident_negative_contamination_disables_the_intent(self):
        policy = fit_policy([prediction(i) for i in range(6)] + [prediction(7, correct=False)], self.backend, "test-scores")
        self.assertGreater(policy["thresholds"]["search"], 1)

    def test_order_disagreements_do_not_contribute_support(self):
        rs = [prediction(i) for i in range(8)]
        for r in rs[4:]:
            r["order_agrees"] = False
        policy = fit_policy(rs, self.backend, "test-scores")
        self.assertGreater(policy["thresholds"]["search"], 1)

    def test_predictions_must_match_exact_question_and_order(self):
        rs = [prediction(i) for i in range(2)]
        requests = [{"id": r["id"], "question": r["question"]} for r in rs]
        validate_outputs(rs, requests)
        with self.assertRaisesRegex(ValueError, "order_mismatch"):
            validate_outputs(rs[::-1], requests)
        changed = copy.deepcopy(rs)
        changed[0]["question"] += " Do not retrieve."
        with self.assertRaisesRegex(ValueError, "question_mismatch"):
            validate_outputs(changed, requests)

    def test_nonfinite_logits_and_truthy_order_flags_are_refused(self):
        r = prediction(1)
        request = {"id": r["id"], "question": r["question"]}
        for field, value in (("logits", {k: float("nan") for k in INTENTS}), ("prompt_tokens", True), ("reverse", 0)):
            bad = copy.deepcopy(r)
            bad["orders"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_outputs([bad], [request])
        r["order_agrees"] = "yes"
        with self.assertRaisesRegex(ValueError, "agreement_invalid"):
            validate_outputs([r], [request])

    def test_abstaining_on_requested_action_is_not_counted_as_success(self):
        actions = [{"expected_intent": "search", "decision": {"calls": [], "effect": "none", "reason": "uncertain"},
                    "intent_correct": True, "pass": False, "unexpected_write": False, "family": "search"}]
        summary = summarize([prediction(1)], actions)
        self.assertEqual(summary["intent_correct"], 1)
        self.assertEqual(summary["positive_action_pass"], 0)
        self.assertEqual(summary["action_contract_pass"], 0)

    def test_wrong_read_is_an_error_even_without_a_write(self):
        actions = [{"expected_intent": "answer", "decision": {"calls": [{"name": "get_device_status", "arguments": {}}],
                    "effect": "read_proposal", "reason": "qualified_intent"}, "intent_correct": False,
                    "pass": False, "unexpected_write": False, "family": "answer"}]
        summary = summarize([prediction(1)], actions)
        self.assertEqual(summary["negative_requests_with_action"], 1)
        self.assertEqual(summary["accepted_plan_correct"], 0)
        self.assertEqual(summary["unrequested_write_proposals"], 0)

    def test_freezes_have_distinct_normalized_questions_and_exact_save_fields(self):
        cases = fixtures("calibration") + fixtures("confirmation")
        self.assertEqual(len(cases), 200)
        self.assertEqual(len({canonical(c["question"]) for c in cases}), 200)
        for case in cases:
            if case["family"] in ("literal_save", "natural_save"):
                self.assertEqual(set(case["expected"][0]["exact"]), {"title", "text"})


if __name__ == "__main__":
    unittest.main()
