"""External-corpus and full-path evidence boundaries, with synthetic input only."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lantern/benchmarks/tool_routing"))
from qualify_admission import evaluate, freeze, summarize_actions, validate_external
from voice_path_metrics import STAGES, summarize_traces, trace_metrics


def corpus():
    return {"schema": "lantern.external-english-actions.v1", "authors": {"external-human": "test-only declaration"},
            "training_authors": ["training-author"], "authorship_review": "test-only review reference",
            "cases": [{"id": "case-1", "question": "Check this Lantern's battery level.",
                       "expected": [{"name": "get_device_status", "exact": {}}], "author": "external-human",
                       "split": "confirmation", "modality": "command", "family": "status", "language": "en"}]}


class CorpusTests(unittest.TestCase):
    def test_file_only_freeze_prediction_scoring_and_tamper_refusal(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = corpus()
            data["cases"].append({**data["cases"][0], "id": "case-2", "split": "calibration",
                                  "question": "Check this device's internet availability."})
            source, history, frozen = root / "input.json", root / "old.jsonl", root / "frozen"
            source.write_text(json.dumps(data))
            history.write_text(json.dumps({"question": "Old regression question."}) + "\n")
            freeze(SimpleNamespace(input=source, history=[history], output=frozen))
            out = root / "evaluation"
            evaluate(SimpleNamespace(freeze=frozen, split="confirmation", transcripts=None, output=out))
            self.assertTrue((out / "outputs.jsonl").exists())
            result = json.loads((out / "RESULT.json").read_text())
            self.assertEqual(result["correct_requested_actions"], 1)
            self.assertFalse(result["qualified"])
            (frozen / "confirmation/requests.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "freeze_changed"):
                evaluate(SimpleNamespace(freeze=frozen, split="confirmation", transcripts=None, output=root / "bad"))

    def test_speech_reference_cannot_be_used_in_place_of_actual_final_asr(self):
        import json
        from run import digest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "confirmation"
            source.mkdir()
            req = source / "requests.json"
            req.write_text(json.dumps([{"id": "speech-1", "question": "Check this device's battery level.", "modality": "human_speech"}]))
            (root / "manifest.json").write_text(json.dumps({"files": {"confirmation/requests.json": digest(req)}, "audio_files": {}}))
            with self.assertRaisesRegex(ValueError, "actual_final_asr"):
                evaluate(SimpleNamespace(freeze=root, split="confirmation", transcripts=None, output=root / "bad"))

    def test_external_authorship_is_required_and_same_author_refused(self):
        data = corpus()
        validate_external(data, set())
        data["training_authors"].append("external-human")
        with self.assertRaisesRegex(ValueError, "same_author"):
            validate_external(data, set())
        data = corpus()
        data["authorship_review"] = ""
        with self.assertRaisesRegex(ValueError, "review_reference"):
            validate_external(data, set())

    def test_old_duplicate_and_cross_split_requests_are_refused(self):
        data = corpus()
        with self.assertRaisesRegex(ValueError, "inspected"):
            validate_external(data, {"check this lantern's battery level."})
        data["cases"].append({**data["cases"][0], "id": "case-2", "split": "calibration"})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_external(data, set())

    def test_real_speech_bytes_and_reference_provenance_are_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "test-only.wav"
            with wave.open(str(p), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\0\0" * 160)
            data = corpus()
            c = data["cases"][0]
            c.update(modality="human_speech", reference_transcriber="test-transcriber",
                     audio={"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "provenance": "test-only"})
            validate_external(data, set())
            p.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "audio"):
                validate_external(data, set())

    def test_abstention_and_wrong_read_are_measured_separately_from_coverage(self):
        blank = {"calls": [], "effect": "none", "reason": "uncertain"}
        actions = [{"expected": [{"name": "search_memory"}], "decision": blank, "pass": False, "unexpected_write": False},
                   {"expected": [], "decision": {"calls": [{"name": "get_device_status"}], "effect": "read_proposal", "reason": "wrong"}, "pass": False, "unexpected_write": False}]
        result = summarize_actions(actions)
        self.assertEqual(result["incorrect_admissions"], 1)
        self.assertEqual(result["useful_action_coverage"], 0)
        self.assertEqual(result["incorrect_save_admissions"], 0)
        result = summarize_actions([actions[0]])
        self.assertIsNone(result["precision"])


def trace():
    return {"request_id": "synthetic-1", "session_binding_sha256": "a" * 64, "profile_sha256": "b" * 64,
            "clock_id": "SYNTHETIC-CLOCK", "hardware": "synthetic-host", "temperature": "warm", "provenance": "synthetic",
            "power_gate_evidence": "SYNTHETIC-NOT-QUALIFIED", "events_ns": {s: i * 1_000_000 for i, s in enumerate(STAGES)}}


class TraceTests(unittest.TestCase):
    def test_full_path_and_review_wait_are_distinct(self):
        result = trace_metrics(trace())
        self.assertEqual(result["eos_to_browser_paint_ms"], 9)
        self.assertEqual(result["review_wait_ms"], 1)
        self.assertEqual(result["submitted_to_first_token_ms"], 5)
        self.assertFalse(summarize_traces([trace()])["physical_acceptance"])

    def test_incomplete_out_of_order_or_truthy_timing_refuses(self):
        for bad in ("missing", "order", "boolean"):
            t = trace()
            if bad == "missing": del t["events_ns"]["receipt_verified"]
            if bad == "order": t["events_ns"]["receipt_verified"] = 100_000_000
            if bad == "boolean": t["events_ns"]["asr_final"] = True
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                trace_metrics(t)

    def test_unapproved_audio_and_mixed_profiles_cannot_be_averaged(self):
        t = trace()
        t["events_ns"]["first_approved_audio"] = 1
        with self.assertRaisesRegex(ValueError, "audio_before"):
            trace_metrics(t)
        t = trace()
        other = copy.deepcopy(t)
        other.update(request_id="synthetic-2", profile_sha256="c" * 64)
        with self.assertRaisesRegex(ValueError, "mixed"):
            summarize_traces([t, other])


if __name__ == "__main__":
    unittest.main()
