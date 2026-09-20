import json
from unittest.mock import patch

import pytest

from sage_lantern.answer_verification import AnswerVerifier, parse_verdict
from test_adapters import FakeResponse


def test_parse_verdict_reads_the_first_line_only():
    assert parse_verdict("OK\nlooks fine", "draft", max_bytes=4096) == ("ok", "draft")
    assert parse_verdict("ok.", "draft", max_bytes=4096) == ("ok", "draft")
    assert parse_verdict("UNSURE", "draft", max_bytes=4096) == ("unsure", "draft")
    assert parse_verdict("CORRECT: The distance is about 500 km.", "draft",
                         max_bytes=4096) == ("corrected", "The distance is about 500 km.")


@pytest.mark.parametrize("raw", [
    None, "", "   ", "MAYBE", "CORRECT:", "CORRECT: <script>", "CORRECT: " + "x" * 5000,
])
def test_parse_verdict_fails_closed(raw):
    status, text = parse_verdict(raw, "draft", max_bytes=4096)
    assert status == "unavailable" and text == "draft"


def test_verifier_returns_a_correction_from_the_model():
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit):
            return json.dumps({"choices": [{"message": {"content": "CORRECT: About 500 km."}}]}).encode()

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    verifier = AnswerVerifier("http://127.0.0.1:8082", "qwen", timeout_seconds=30)
    with patch("urllib.request.urlopen", fake_urlopen):
        verdict = verifier.verify("How far is it?", "The distance is 130 km.")
    assert verdict.status == "corrected" and verdict.text == "About 500 km."
    assert captured["body"]["messages"][1]["content"].endswith("The distance is 130 km.")
    assert captured["timeout"] == 30


@pytest.mark.parametrize("error", [TimeoutError(), ValueError("bad json")])
def test_verifier_failures_leave_the_original_answer(error):
    verifier = AnswerVerifier("http://127.0.0.1:8082", "qwen")
    with patch("urllib.request.urlopen", side_effect=error):
        verdict = verifier.verify("Q", "original answer")
    assert verdict.status == "unavailable" and verdict.text == "original answer"
