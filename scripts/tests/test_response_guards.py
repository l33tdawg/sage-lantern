"""Deterministic RKLLM response-guard tests; no model or device calls."""
from __future__ import annotations

import importlib.util
import io
import json
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "software/npu/response_guards.py"
SPEC = importlib.util.spec_from_file_location("response_guards_test", MODULE_PATH)
GUARDS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARDS)
SERVER_PATH = ROOT / "software/npu/rkllm_server.py"
SERVER_SPEC = importlib.util.spec_from_file_location("rkllm_server_guards_test", SERVER_PATH)
SERVER = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(SERVER)


def interval_messages(x, ex, y, ey):
    return [{
        "role": "user",
        "content": json.dumps({
            "scenario": (
                "In a fictional Palegrove counting exercise, Line X's observed count is "
                f"{x} units in one ten-minute interval, with an absolute error bound of "
                f"±{ex} units. Line Y's observed count is the authoritative line_y_count "
                "for the same interval length, with an absolute error bound of "
                f"±{ey} units. Counts are integers. Multiplying a ten-minute count by 6 "
                "gives a normalized units-per-hour rate."
            ),
            "request": "Give the possible count and rate intervals.",
            "authoritative_update": {"line_y_count": y},
        }),
    }]


class IntervalGuardTests(unittest.TestCase):
    def test_overlap(self):
        text = GUARDS.interval_answer(interval_messages(47, 2, 45, 3))
        self.assertIn("45–49", text)
        self.assertIn("270–294", text)
        self.assertIn("252–288", text)
        self.assertIn("Neither line is guaranteed", text)

    def test_guaranteed(self):
        text = GUARDS.interval_answer(interval_messages(47, 2, 53, 3))
        self.assertIn("Line Y is guaranteed", text)
        self.assertIn("minimum guaranteed gap is 1 unit", text)

    def test_non_interval_passes(self):
        self.assertIsNone(GUARDS.interval_answer([
            {"role": "user", "content": json.dumps({"request": "hello"})}]))


class StatusGuardTests(unittest.TestCase):
    def messages(self, status):
        return [
            {"role": "user", "content": json.dumps({"action": "recall", "query": "stencil"})},
            {"role": "tool", "name": "sage_recall",
             "content": json.dumps({"status": status, "matches": []})},
            {"role": "user", "content": json.dumps({"request": "Does it exist?"})},
        ]

    def test_failed_lookup_repair(self):
        text = GUARDS.status_text(
            self.messages("error"),
            "The lookup failed. Therefore no matching memory exists.")
        self.assertIn("does not establish absence", text)

    def test_committed_passes(self):
        original = "The write is committed."
        self.assertEqual(GUARDS.status_text(self.messages("committed"), original), original)

    def test_unrelated_failed_lookup_prose_is_replaced(self):
        text = GUARDS.status_text(
            self.messages("error"),
            "The word stencil is not a proper noun or a defined thing.")
        self.assertIn("does not establish absence", text)

    def test_successful_empty_universal_absence_is_repaired(self):
        text = GUARDS.status_text(
            self.messages("success"),
            "No matching memory exists.")
        self.assertIn("not universal absence", text)

    def test_failed_recall_receipt_does_not_answer_save_question(self):
        messages = [
            {"role": "user", "content": json.dumps({"action": "recall", "query": "stencil"})},
            {"role": "tool", "name": "sage_recall",
             "content": json.dumps({"request_id": "read-1", "status": "error"})},
            {"role": "user", "content": "Did the save complete?"},
        ]
        self.assertIsNone(GUARDS.receipt_status_answer(messages))

    def test_save_status_skips_unrelated_recall_receipt(self):
        messages = [
            {"role": "user", "content": json.dumps({
                "action": "remember",
                "content": "The launch checklist is in the blue folder.",
            })},
            {"role": "tool", "name": "sage_remember",
             "content": json.dumps({"request_id": "write-1", "status": "committed"})},
            {"role": "tool", "name": "sage_recall",
             "content": json.dumps({"request_id": "read-1", "status": "error"})},
            {"role": "user", "content": "Did the save complete?"},
        ]
        self.assertEqual(
            GUARDS.receipt_status_answer(messages),
            "The save is committed; completion is established.")

    def test_recall_status_ignores_later_save_receipt(self):
        messages = [
            {"role": "user", "content": json.dumps({"action": "recall", "query": "stencil"})},
            {"role": "tool", "name": "sage_recall",
             "content": json.dumps({"status": "error", "matches": []})},
            {"role": "user", "content": json.dumps({
                "action": "remember",
                "content": "A later unrelated save.",
            })},
            {"role": "tool", "name": "sage_remember",
             "content": json.dumps({"status": "committed"})},
            {"role": "user", "content": json.dumps({"request": "Was the stencil lookup successful?"})},
        ]
        # The latest actionable request is the save, so the old recall receipt
        # cannot be used to repair or answer this turn.
        self.assertEqual(
            GUARDS.status_text(messages, "No matching memory exists."),
            "No matching memory exists.")

    def test_save_request_with_status_word_is_not_a_status_question(self):
        messages = [
            {"role": "user", "content": json.dumps({
                "request": "The content is ready to save.",
                "action": "remember",
                "content": "The launch checklist is in the blue folder.",
            })},
        ]
        self.assertIsNone(GUARDS.receipt_status_answer(messages))

    def test_new_save_question_is_not_answered_from_old_receipt(self):
        messages = [
            {"role": "user", "content": json.dumps({
                "action": "remember",
                "content": "The first launch code is amber.",
            })},
            {"role": "tool", "name": "sage_remember",
             "content": json.dumps({"request_id": "write-1", "status": "committed"})},
            {"role": "user", "content": json.dumps({
                "request": (
                    "Is this the right content to save? Please remember the new "
                    "launch code is bluebird."
                ),
                "action": "remember",
                "content": "The new launch code is bluebird.",
            })},
        ]
        self.assertIsNone(GUARDS.receipt_status_answer(messages))

    def test_unnamed_receipt_does_not_bind_to_save(self):
        messages = [
            {"role": "user", "content": json.dumps({
                "action": "remember",
                "content": "The launch checklist is in the blue folder.",
            })},
            {"role": "tool",
             "content": json.dumps({"request_id": "read-1", "status": "committed"})},
            {"role": "user", "content": "Did the save complete?"},
        ]
        self.assertIsNone(GUARDS.receipt_status_answer(messages))

    def test_recall_followup_phrasings(self):
        base = [
            {"role": "user", "content": json.dumps({"action": "recall", "query": "stencil"})},
            {"role": "tool", "name": "sage_recall",
             "content": json.dumps({"status": "error", "matches": []})},
        ]
        for text in ("Did it fail?", "Was anything returned?",
                     "Did it come back empty?", "Did you find anything?",
                     "Does the stencil exist?", "Is there a match?",
                     "Do you have a match?", "hello"):
            with self.subTest(text=text):
                self.assertTrue(GUARDS.needs_post_guard(
                    base + [{"role": "user", "content": text}]))

    def test_only_first_turn_after_recall_receipt_is_buffered(self):
        messages = [
            {"role": "user", "content": json.dumps({"action": "recall", "query": "stencil"})},
            {"role": "tool", "name": "sage_recall",
             "content": json.dumps({"status": "success", "matches": []})},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hello."},
            {"role": "user", "content": "What is the result of 2+2?"},
        ]
        self.assertFalse(GUARDS.needs_post_guard(messages))


class LongFormGuardTests(unittest.TestCase):
    def test_detects_explicit_paragraph_count(self):
        messages = [{"role": "user", "content": "Write exactly four paragraphs about this."}]
        self.assertEqual(GUARDS.requested_paragraphs(messages), 4)
        self.assertIsNone(GUARDS.requested_paragraphs(
            [{"role": "user", "content": "Write one paragraph."}]))

    def test_normalizes_more_blocks_to_requested_count(self):
        text = "\n\n".join(
            ["First block.", "Second block.", "Third block.", "Fourth block.",
             "Fifth block.", "Sixth block."])
        result = GUARDS.normalize_paragraphs(text, 4)
        self.assertEqual(len(result.split("\n\n")), 4)
        self.assertIn("First block.", result)
        self.assertIn("Sixth block.", result)

    def test_normalizes_fewer_blocks_without_inventing_content(self):
        text = (
            "First sentence. Second sentence. Third sentence. "
            "Fourth sentence. Fifth sentence."
        )
        result = GUARDS.normalize_paragraphs(text, 3)
        self.assertEqual(len(result.split("\n\n")), 3)
        self.assertEqual(result.replace("\n\n", " "), text)

    def test_headings_are_removed(self):
        text = "### Heading\n\nOne paragraph.\n\n### Another\n\nTwo."
        result = GUARDS.normalize_paragraphs(text, 2)
        self.assertNotIn("###", result)
        self.assertIn("One paragraph.", result)
        self.assertIn("Two.", result)

    def test_longform_stream_is_buffered_and_normalized(self):
        class FakeEngine:
            run_calls = 0
            stream_calls = 0
            def run(self, prompt, max_new_tokens):
                self.run_calls += 1
                return {"ok": True,
                        "text": "One sentence. Two sentence. Three sentence.",
                        "prefill_tokens": 4, "generate_tokens": 3}
            def stream(self, *args):
                self.stream_calls += 1
                raise AssertionError("long-form turns must be normalized before streaming")

        raw = json.dumps({
            "messages": [{"role": "user", "content": "Write exactly three paragraphs."}],
            "max_tokens": 8192,
            "stream": True,
        }).encode()
        handler = SERVER.Handler.__new__(SERVER.Handler)
        handler.path = "/v1/chat/completions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.close_connection = False
        handler.model_name = "test-model"
        handler.fallback_url = None
        handler.response_guards = True
        engine = FakeEngine()
        handler.engine = engine
        handler.do_POST()
        body = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("One sentence.", body)
        self.assertIn("Three sentence.", body)
        self.assertEqual(engine.run_calls, 1)
        self.assertEqual(engine.stream_calls, 0)

    def test_detects_explicit_bullet_count(self):
        self.assertEqual(GUARDS.requested_bullets(
            [{"role": "user", "content": "Write a four-bullet volunteer update."}]), 4)
        self.assertEqual(GUARDS.requested_bullets(
            [{"role": "user", "content": "Write exactly four bullets."}]), 4)
        self.assertEqual(GUARDS.requested_bullets(
            [{"role": "user", "content": "Give me three bullet points."}]), 3)
        self.assertIsNone(GUARDS.requested_bullets(
            [{"role": "user", "content": "Write one paragraph."}]))

    def test_repeated_bullet_is_not_published_twice(self):
        text = (
            "• Safety reviews are scheduled for Friday.\n"
            "• The intake label for tools is teal.\n"
            "• High-risk tests require two people.\n"
            "• Safety reviews are scheduled for Friday."
        )
        result = GUARDS.normalize_bullets(text, 4)
        self.assertEqual(result.count("Safety reviews are scheduled for Friday."), 1)
        self.assertEqual(result.count("\n") + 1, 3)
        self.assertIn("intake label for tools is teal", result)

    def test_distinct_bullet_beyond_a_duplicate_is_kept(self):
        text = (
            "- Friday reviews.\n"
            "- Teal intake label.\n"
            "- Two-person test.\n"
            "- Friday reviews.\n"
            "- Monthly maintenance."
        )
        result = GUARDS.normalize_bullets(text, 4)
        self.assertEqual(result.count("\n") + 1, 4)
        self.assertIn("Monthly maintenance.", result)
        self.assertEqual(result.count("Friday reviews."), 1)

    def test_retired_contrast_is_removed_when_excluded(self):
        messages = [
            {"role": "user", "content": (
                "Correction: safety reviews now happen every Friday, not Wednesday, "
                "and the intake label is teal, not amber.")},
            {"role": "user", "content": (
                "Write the kickoff note. Do not mention the retired label or retired day.")},
        ]
        text = (
            "The intake label is teal, not amber. Safety reviews now happen every "
            "Friday, not Wednesday. The two-person rule remains."
        )
        result = GUARDS.longform_text(messages, text)
        self.assertNotIn("amber", result.lower())
        self.assertNotIn("Wednesday", result)
        self.assertIn("teal", result)
        self.assertIn("Friday", result)
        self.assertIn("two-person", result)

    def test_retired_value_survives_when_user_asks_what_changed(self):
        messages = [
            {"role": "user", "content": (
                "Correction: safety reviews now happen every Friday, not Wednesday, "
                "and the intake label is teal, not amber.")},
            {"role": "user", "content": "In one sentence, state what changed."},
        ]
        text = "The intake label changed from amber to teal."
        self.assertEqual(GUARDS.longform_text(messages, text), text)

    def test_retired_from_to_contrast_is_shortened(self):
        messages = [
            {"role": "user", "content": (
                "Correction: the safety day is now Friday, not Wednesday, and the "
                "intake label is teal, not amber.")},
            {"role": "user", "content": "Do not mention the retired values."},
        ]
        result = GUARDS.strip_retired_mentions(
            messages, "The intake label changed from amber to teal.")
        self.assertNotIn("amber", result.lower())
        self.assertIn("changed to teal", result)

    def test_current_retired_label_claim_is_corrected(self):
        messages = [
            {"role": "user", "content": (
                "Correction: safety reviews now happen every Friday, not Wednesday, "
                "and the intake label is teal, not amber.")},
            {"role": "user", "content": "In one sentence, state what changed."},
        ]
        result = GUARDS.longform_text(
            messages, "The intake label is amber and safety reviews are Friday.")
        self.assertIn("The intake label is teal", result)
        self.assertNotIn("label is amber", result.lower())

    def test_historical_retired_claim_is_not_rewritten(self):
        messages = [
            {"role": "user", "content": (
                "Correction: safety reviews now happen every Friday, not Wednesday, "
                "and the intake label is teal, not amber.")},
            {"role": "user", "content": "In one sentence, state what changed."},
        ]
        text = "The intake label was amber before the correction."
        self.assertEqual(GUARDS.longform_text(messages, text), text)

    def test_explicit_bullet_request_is_buffered(self):
        messages = [{"role": "user", "content": "Write exactly four bullets."}]
        self.assertTrue(GUARDS.needs_post_guard(messages))

    def test_paragraph_request_flattens_stray_bullets(self):
        messages = [{"role": "user", "content": "Write exactly two paragraphs."}]
        text = (
            "Operations note:\n\n"
            "- The intake label is amber.\n"
            "- High-risk tests need two people.\n"
            "- Safety reviews are Wednesday."
        )
        result = GUARDS.longform_text(messages, text)
        self.assertNotRegex(result, r"(?m)^\s*[-*•]\s+")
        self.assertEqual(len(result.split("\n\n")), 2)
        self.assertIn("High-risk tests need two people.", result)

    def test_bullet_request_keeps_bullet_lines(self):
        messages = [{"role": "user", "content": "Write exactly three bullets."}]
        text = "- First point.\n- Second point.\n- Third point."
        result = GUARDS.longform_text(messages, text)
        self.assertEqual(result, text)


class ServerWiringTests(unittest.TestCase):
    def test_render_chat_accepts_tool_receipt(self):
        rendered = SERVER.render_chat([
            {"role": "user", "content": "question"},
            {"role": "tool", "content": '{"status":"error"}'},
        ])
        self.assertIn("<|im_start|>tool\n{\"status\":\"error\"}<|im_end|>", rendered)

    def test_server_guard_loader_and_default_flag(self):
        self.assertFalse(SERVER.Handler.response_guards)
        text = SERVER.response_guards().interval_answer(interval_messages(47, 2, 45, 3))
        self.assertIn("270–294", text)

    def test_server_guard_loader_is_cached(self):
        self.assertIs(SERVER.response_guards(), SERVER.response_guards())

    def test_short_circuit_stream_shape(self):
        handler = SERVER.Handler.__new__(SERVER.Handler)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.model_name = "test-model"
        handler._stream_text("Line X can have 45–49 units.", time.time())
        body = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("Line X can have 45–49 units.", body)
        self.assertIn('"finish_reason": "stop"', body)
        self.assertTrue(body.endswith("data: [DONE]\n\n"))

    def test_interval_request_does_not_call_engine(self):
        class FailEngine:
            calls = 0
            def run(self, *args):
                self.calls += 1
                raise AssertionError("engine must not run for a deterministic interval answer")
            def stream(self, *args):
                self.calls += 1
                raise AssertionError("engine must not stream")

        raw = json.dumps({"messages": interval_messages(47, 2, 45, 3),
                          "max_tokens": 256, "stream": False}).encode()
        handler = SERVER.Handler.__new__(SERVER.Handler)
        handler.path = "/v1/chat/completions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.model_name = "test-model"
        handler.fallback_url = None
        handler.response_guards = True
        engine = FailEngine()
        handler.engine = engine
        handler.do_POST()
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        self.assertIn("270–294", content)
        self.assertIn("Neither line is guaranteed", content)
        self.assertEqual(engine.calls, 0)

    def test_nonstream_failed_lookup_is_repaired_before_publish(self):
        class FakeEngine:
            calls = 0
            def run(self, prompt, max_new_tokens):
                self.calls += 1
                return {"ok": True,
                        "text": "The lookup failed. Therefore no matching memory exists.",
                        "prefill_tokens": 10, "generate_tokens": 8}

        messages = [
            {"role": "system", "content": "{}"},
            {"role": "user", "content": json.dumps({"action": "recall", "query": "stencil"})},
            {"role": "tool", "name": "sage_recall",
             "content": json.dumps({"status": "error", "matches": []})},
            {"role": "user", "content": json.dumps({"request": "Does it exist?"})},
        ]
        raw = json.dumps({"messages": messages, "max_tokens": 256, "stream": False}).encode()
        handler = SERVER.Handler.__new__(SERVER.Handler)
        handler.path = "/v1/chat/completions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.model_name = "test-model"
        handler.fallback_url = None
        handler.response_guards = True
        engine = FakeEngine()
        handler.engine = engine
        handler.do_POST()
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        self.assertIn("does not establish absence", content)
        self.assertNotIn("Therefore no matching memory exists", content)
        self.assertEqual(engine.calls, 1)

    def test_stream_failed_lookup_is_buffered_and_repaired(self):
        class FakeEngine:
            run_calls = 0
            stream_calls = 0
            def run(self, prompt, max_new_tokens):
                self.run_calls += 1
                return {"ok": True,
                        "text": "The lookup failed. Therefore no matching memory exists.",
                        "prefill_tokens": 10, "generate_tokens": 8}
            def stream(self, *args):
                self.stream_calls += 1
                raise AssertionError("guarded recall output must be buffered")

        messages = [
            {"role": "system", "content": "{}"},
            {"role": "user", "content": json.dumps({"action": "recall", "query": "stencil"})},
            {"role": "tool", "name": "sage_recall",
             "content": json.dumps({"status": "error", "matches": []})},
            {"role": "user", "content": json.dumps({"request": "Does it exist?"})},
        ]
        raw = json.dumps({"messages": messages, "max_tokens": 256, "stream": True}).encode()
        handler = SERVER.Handler.__new__(SERVER.Handler)
        handler.path = "/v1/chat/completions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.close_connection = False
        handler.model_name = "test-model"
        handler.fallback_url = None
        handler.response_guards = True
        engine = FakeEngine()
        handler.engine = engine
        handler.do_POST()
        body = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("does not establish absence", body)
        self.assertNotIn("Therefore no matching memory exists", body)
        self.assertEqual(engine.run_calls, 1)
        self.assertEqual(engine.stream_calls, 0)

    def test_ordinary_chat_still_streams(self):
        class FakeEngine:
            run_calls = 0
            stream_calls = 0
            def run(self, *args):
                self.run_calls += 1
                raise AssertionError("ordinary chat must stay streamed")
            def stream(self, prompt, max_new_tokens, want_tokens=True):
                self.stream_calls += 1
                yield {"event": "token", "text": "hello"}
                yield {"event": "done", "ok": True, "text": "hello",
                       "prefill_tokens": 4, "generate_tokens": 1}

        raw = json.dumps({
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 16,
            "stream": True,
        }).encode()
        handler = SERVER.Handler.__new__(SERVER.Handler)
        handler.path = "/v1/chat/completions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.close_connection = False
        handler.model_name = "test-model"
        handler.fallback_url = None
        handler.response_guards = True
        engine = FakeEngine()
        handler.engine = engine
        handler.do_POST()
        body = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("hello", body)
        self.assertEqual(engine.run_calls, 0)
        self.assertEqual(engine.stream_calls, 1)

    def test_chat_after_the_first_post_recall_turn_still_streams(self):
        class FakeEngine:
            run_calls = 0
            stream_calls = 0
            def run(self, *args):
                self.run_calls += 1
                raise AssertionError("ordinary chat after recall must stay streamed")
            def stream(self, prompt, max_new_tokens, want_tokens=True):
                self.stream_calls += 1
                yield {"event": "token", "text": "hello"}
                yield {"event": "done", "ok": True, "text": "hello",
                       "prefill_tokens": 4, "generate_tokens": 1}

        messages = [
            {"role": "user", "content": json.dumps({"action": "recall", "query": "stencil"})},
            {"role": "tool", "name": "sage_recall",
             "content": json.dumps({"status": "success", "matches": []})},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hello."},
            {"role": "user", "content": "What is the result of 2+2?"},
        ]
        raw = json.dumps({"messages": messages, "max_tokens": 16, "stream": True}).encode()
        handler = SERVER.Handler.__new__(SERVER.Handler)
        handler.path = "/v1/chat/completions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.close_connection = False
        handler.model_name = "test-model"
        handler.fallback_url = None
        handler.response_guards = True
        engine = FakeEngine()
        handler.engine = engine
        handler.do_POST()
        self.assertEqual(engine.run_calls, 0)
        self.assertEqual(engine.stream_calls, 1)

    def test_recall_result_questions_keep_expected_streaming_mode(self):
        messages_base = [
            {"role": "user", "content": json.dumps({"action": "recall", "query": "stencil"})},
            {"role": "tool", "name": "sage_recall",
             "content": json.dumps({"status": "error", "matches": []})},
        ]

        for text, buffered in (
                ("Did it fail?", True),
                ("Does the stencil exist?", True),
                ("Is there a match?", True),
                ("What is the result of 2+2?", True)):
            with self.subTest(text=text):
                class FakeEngine:
                    run_calls = 0
                    stream_calls = 0
                    def run(self, prompt, max_new_tokens):
                        self.run_calls += 1
                        if not buffered:
                            raise AssertionError("ordinary chat must stay streamed")
                        return {"ok": True,
                                "text": "The lookup failed. Therefore no matching memory exists.",
                                "prefill_tokens": 10, "generate_tokens": 8}
                    def stream(self, prompt, max_new_tokens, want_tokens=True):
                        self.stream_calls += 1
                        if buffered:
                            raise AssertionError("recall follow-up must be buffered")
                        yield {"event": "token", "text": "answer"}
                        yield {"event": "done", "ok": True, "text": "answer",
                               "prefill_tokens": 4, "generate_tokens": 1}

                messages = messages_base + [{"role": "user", "content": text}]
                raw = json.dumps({
                    "messages": messages, "max_tokens": 32, "stream": True}).encode()
                handler = SERVER.Handler.__new__(SERVER.Handler)
                handler.path = "/v1/chat/completions"
                handler.headers = {"Content-Length": str(len(raw))}
                handler.rfile = io.BytesIO(raw)
                handler.wfile = io.BytesIO()
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                handler.close_connection = False
                handler.model_name = "test-model"
                handler.fallback_url = None
                handler.response_guards = True
                engine = FakeEngine()
                handler.engine = engine
                handler.do_POST()
                self.assertEqual(engine.run_calls, int(buffered))
                self.assertEqual(engine.stream_calls, int(not buffered))

    def test_new_save_question_reaches_engine(self):
        class FakeEngine:
            stream_calls = 0
            def run(self, *args):
                raise AssertionError("stream request must use engine.stream")
            def stream(self, prompt, max_new_tokens, want_tokens=True):
                self.stream_calls += 1
                yield {"event": "token", "text": "<tool_call>{}</tool_call>"}
                yield {"event": "done", "ok": True, "text": "<tool_call>{}</tool_call>",
                       "prefill_tokens": 4, "generate_tokens": 2}

        messages = [
            {"role": "user", "content": json.dumps({
                "action": "remember",
                "content": "The first launch code is amber.",
            })},
            {"role": "tool", "name": "sage_remember",
             "content": json.dumps({"request_id": "write-1", "status": "committed"})},
            {"role": "user", "content": json.dumps({
                "request": (
                    "Is this the right content to save? Please remember the new "
                    "launch code is bluebird."
                ),
                "action": "remember",
                "content": "The new launch code is bluebird.",
            })},
        ]
        raw = json.dumps({"messages": messages, "max_tokens": 32, "stream": True}).encode()
        handler = SERVER.Handler.__new__(SERVER.Handler)
        handler.path = "/v1/chat/completions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.close_connection = False
        handler.model_name = "test-model"
        handler.fallback_url = None
        handler.response_guards = True
        engine = FakeEngine()
        handler.engine = engine
        handler.do_POST()
        self.assertEqual(engine.stream_calls, 1)

    def test_malformed_messages_are_rejected(self):
        raw = json.dumps({"messages": [42], "stream": False}).encode()
        handler = SERVER.Handler.__new__(SERVER.Handler)
        handler.path = "/v1/chat/completions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_POST()
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(handler.send_response.call_args.args[0], 400)
        self.assertIn("list of objects", payload["error"]["message"])


class PrivateOwnerGuardTests(unittest.TestCase):
    def messages(self):
        system = json.dumps({"instructions": "policy", "routing": {
            "owned_home": "self", "permitted_public_domains": [],
            "private_grants": []}})
        request = json.dumps({
            "request": "Save this exact fact in Talven's private memory.",
            "action": "remember",
            "content": "The silver practice token is inside the saffron drawstring pouch.",
            "memory_type": "fact", "memory_owner": "Talven"})
        return [{"role": "system", "content": system},
                {"role": "user", "content": request}]

    def test_private_owner_without_grant_is_refused(self):
        refusal = GUARDS.private_owner_refusal(self.messages())
        self.assertIn("no trusted grant", refusal)

    def test_tool_call_is_blocked(self):
        text = GUARDS.action_policy(
            self.messages(),
            '<tool_call>{"name":"sage_remember","arguments":{"content":"x","type":"fact"}}</tool_call>')
        self.assertIn("no trusted grant", text)

    def test_authorize_save_true_is_refused(self):
        messages = self.messages()
        request = json.loads(messages[-1]["content"])
        request.pop("action")
        request["authorize_save"] = True
        messages[-1]["content"] = json.dumps(request)
        self.assertIn("no trusted grant", GUARDS.private_owner_refusal(messages))

    def test_committed_completion_claim_is_blocked(self):
        text = GUARDS.action_policy(self.messages(), "I have committed the save.")
        self.assertIn("no trusted grant", text)

    def test_completion_claim_variants_are_blocked(self):
        for text in (
                "I’ve saved it.",
                "I’ve remembered it.",
                "I remembered it.",
                "I have memorised it.",
                "I memorized it.",
                "I noted it.",
                "Noted.",
                "Remembered."):
            with self.subTest(text=text):
                self.assertIn(
                    "no trusted grant", GUARDS.action_policy(self.messages(), text))

    def test_private_owner_status_followup_is_refused(self):
        messages = self.messages()
        messages.append({"role": "user", "content": "Did the save complete?"})
        self.assertIn("no trusted grant", GUARDS.private_owner_refusal(messages))

    def test_natural_language_private_owner_is_refused(self):
        messages = [
            {"role": "system", "content": json.dumps({"routing": {
                "owned_home": "self", "permitted_public_domains": [],
                "private_grants": []}})},
            {"role": "user",
             "content": "Save this in Talven's private memory: the spare key is under the mat."},
        ]
        self.assertIn("no trusted grant", GUARDS.private_owner_refusal(messages))
        self.assertIn(
            "no trusted grant",
            GUARDS.action_policy(
                messages,
                '<tool_call>{"name":"sage_remember","arguments":'
                '{"content":"the spare key is under the mat.","type":"fact"}}'
                "</tool_call>",
            ),
        )

    def test_natural_language_private_owner_status_followup_is_refused(self):
        messages = [
            {"role": "system", "content": json.dumps({"routing": {
                "owned_home": "self", "permitted_public_domains": [],
                "private_grants": []}})},
            {"role": "user",
             "content": "Save this in Talven’s private memory: the spare key is under the mat."},
            {"role": "user", "content": "Did the save complete?"},
        ]
        self.assertIn("no trusted grant", GUARDS.private_owner_refusal(messages))

    def test_committed_receipt_status(self):
        messages = self.messages()
        messages += [
            {"role": "assistant", "content": "saved"},
            {"role": "tool", "name": "sage_remember",
             "content": json.dumps({"status": "committed"})},
            {"role": "user", "content": "Did the save complete?"},
        ]
        answer = GUARDS.receipt_status_answer(messages)
        self.assertEqual(answer, "The save is committed; completion is established.")


class LabelGuardTests(unittest.TestCase):
    def test_undefined_label_is_short_circuited(self):
        answer = GUARDS.label_answer([
            {"role": "user",
             "content": "What does the glaucous label mean in the rope walk?"},
        ])
        self.assertIn("isn't specified", answer)
        self.assertIn("What does glaucous mean", answer)
        self.assertNotIn("coated", answer)

    def test_undefined_label_without_the_word_label(self):
        answer = GUARDS.label_answer([
            {"role": "user", "content": "What does teal signify on the intake sheet?"},
        ])
        self.assertIn("What does teal mean", answer)

    def test_undefined_label_with_see_phrasing(self):
        answer = GUARDS.label_answer([
            {"role": "user",
             "content": "I see teal on the sample tray. What does it indicate?"},
        ])
        self.assertIn("What does teal mean", answer)

    def test_same_turn_definition_is_left_to_the_model(self):
        self.assertIsNone(GUARDS.label_answer([
            {"role": "user",
             "content": "In the bindery, glaucous means scrapped after the leak. "
                        "What does the glaucous label mean?"},
        ]))

    def test_general_knowledge_question_is_not_a_label_question(self):
        self.assertIsNone(GUARDS.label_answer([
            {"role": "user", "content": "What does photosynthesis mean?"},
        ]))
        self.assertIsNone(GUARDS.label_answer([
            {"role": "user", "content": "What does a red traffic light mean?"},
        ]))


class CadenceGuardTests(unittest.TestCase):
    def test_generic_check_cadence_fails_closed(self):
        answer = GUARDS.cadence_answer([
            {"role": "user", "content": "How often is the check done?"},
        ])
        self.assertEqual(
            answer,
            "The check interval isn't specified here. What interval should apply?",
        )

    def test_supplied_cadence_is_not_intercepted(self):
        self.assertIsNone(GUARDS.cadence_answer([
            {"role": "user",
             "content": "The safety review runs every Wednesday. How often is the check done?"},
        ]))
        self.assertIsNone(GUARDS.cadence_answer([
            {"role": "user",
             "content": "We do a weekly inspection. How frequently should we review it?"},
        ]))

    def test_general_knowledge_frequency_is_not_intercepted(self):
        self.assertIsNone(GUARDS.cadence_answer([
            {"role": "user", "content": "How often does the moon orbit Earth?"},
        ]))

    def test_non_cadence_question_is_not_intercepted(self):
        self.assertIsNone(GUARDS.cadence_answer([
            {"role": "user", "content": "What should the check cover?"},
        ]))


if __name__ == "__main__":
    unittest.main()
