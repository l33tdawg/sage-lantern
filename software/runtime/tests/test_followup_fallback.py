"""The second, metadata-only request for follow-up questions.

The in-band trailer is free when the model complies and silently absent when it
does not — which is exactly the short answers the bubbles are meant for. These
tests pin the fallback: one bounded call, off the turn's critical path so it runs
while the answer is being spoken, and never able to damage an answer that is
already correct or to hold a turn open.
"""
import io
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from sage_lantern.adapters import (SUGGESTION_MAX_BYTES, SUGGESTION_MAX_TOKENS, SUGGESTION_TIMEOUT_SECONDS,
                                   LlamaCppHTTP)
from sage_lantern.orchestrator import VoiceOrchestrator
from sage_lantern.state import JsonlEventLog, StateMachine

QUESTION = "What is the capital of France?"
ANSWER = "Paris"
FROM_TRAILER = ["What is the population of Paris?", "When is the best time to visit Paris?"]
FROM_SECOND_CALL = ["How many people live in Paris?", "What is the weather like in Paris?"]


def completion(text: str, *, finish_reason: str = "stop") -> bytes:
    return json.dumps({"choices": [{"message": {"content": text}, "finish_reason": finish_reason}]}).encode()


class Body:
    status = 200

    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return io.BytesIO(self.payload)

    def __exit__(self, exc_type, exc, traceback):
        return False


def adapter(**kwargs) -> LlamaCppHTTP:
    return LlamaCppHTTP("http://127.0.0.1:8081", "lantern-native5-2b", **kwargs)


# --- the request itself ---------------------------------------------------

def test_no_request_is_made_while_the_feature_is_off():
    calls = []

    def record(request, timeout):
        calls.append(request)
        return Body(completion("[]"))

    with patch("urllib.request.urlopen", record):
        assert adapter().suggest(QUESTION, ANSWER) == []
    assert calls == [], "the second call must not exist behind a disabled feature"


def test_the_asked_for_json_array_is_parsed():
    body = completion(json.dumps(FROM_SECOND_CALL))
    with patch("urllib.request.urlopen", return_value=Body(body)):
        assert adapter(experimental_answer_suggestions=True).suggest(QUESTION, ANSWER) == FROM_SECOND_CALL


def test_the_one_question_per_line_shape_the_model_actually_writes_is_accepted():
    """Measured on unit C: the 2B answers with two bare lines, no JSON, no bullets."""
    body = completion("How many people live in New York?\nWhat is the population of Tokyo?")
    with patch("urllib.request.urlopen", return_value=Body(body)):
        assert adapter(experimental_answer_suggestions=True).suggest(QUESTION, ANSWER) == [
            "How many people live in New York?", "What is the population of Tokyo?"]


def test_a_meta_reply_the_model_sometimes_writes_is_not_a_suggestion():
    body = completion("I suggest you ask about the population.")
    with patch("urllib.request.urlopen", return_value=Body(body)):
        assert adapter(experimental_answer_suggestions=True).suggest(QUESTION, ANSWER) == []


def test_a_request_is_bounded_in_tokens_time_and_size():
    captured = {}

    def record(request, timeout):
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Body(completion(json.dumps(FROM_SECOND_CALL)))

    with patch("urllib.request.urlopen", record):
        adapter(experimental_answer_suggestions=True).suggest(QUESTION, ANSWER)

    assert captured["body"]["max_tokens"] == SUGGESTION_MAX_TOKENS, \
        "the 8.2 s repetition case must be capped, not hoped against"
    assert captured["timeout"] == SUGGESTION_TIMEOUT_SECONDS
    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    prompt = captured["body"]["messages"][1]["content"]
    assert QUESTION in prompt and ANSWER in prompt


def test_a_reply_larger_than_the_bound_is_refused():
    body = completion("x" * (SUGGESTION_MAX_BYTES + 1))
    with patch("urllib.request.urlopen", return_value=Body(body)):
        assert adapter(experimental_answer_suggestions=True).suggest(QUESTION, ANSWER) == []


@pytest.mark.parametrize("failure", [OSError("unreachable"), TimeoutError("slow"), ValueError("bad json")])
def test_every_failure_mode_resolves_to_no_suggestions(failure):
    with patch("urllib.request.urlopen", side_effect=failure):
        assert adapter(experimental_answer_suggestions=True).suggest(QUESTION, ANSWER) == []


# --- the turn -------------------------------------------------------------

class FakeSTT:
    def transcribe(self, audio_path):
        return QUESTION


class FakeContext:
    def recall(self, query):
        return []


class FakeTTS:
    def synthesize(self, text, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFFfake")


class Player:
    def play(self, audio_path):
        return self

    def wait(self):
        return None


class ChattyLLM:
    """Answers, and optionally offers a second-call path."""

    def __init__(self, *, trailer=None, second=None, delay=0.0, error=None):
        self.last_suggestions = list(trailer or [])
        self.second = second
        self.delay = delay
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def respond(self, query, context):
        return ANSWER

    def suggest(self, question, answer):
        self.calls.append((question, answer))
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return list(self.second or [])


def turn(tmp_path, llm, *, playback=None, image_path=None):
    orchestrator = VoiceOrchestrator(
        StateMachine(JsonlEventLog(tmp_path / "state.jsonl")),
        FakeSTT(), FakeContext(), llm, FakeTTS(), tmp_path, None,
        playback=playback or Player())
    audio = tmp_path / "question.wav"
    audio.write_bytes(b"RIFFfake")
    return orchestrator.run_audio(audio, image_path=image_path)


def test_a_trailer_the_model_already_wrote_costs_no_second_call(tmp_path):
    llm = ChattyLLM(trailer=FROM_TRAILER, second=FROM_SECOND_CALL)
    result = turn(tmp_path, llm)
    assert result.follow_up_questions == FROM_TRAILER
    assert llm.calls == [], "the fallback must stay dormant when the trailer arrived"
    assert "suggestions" not in result.timings_ms


def test_a_short_answer_gets_its_bubbles_from_the_second_call(tmp_path):
    llm = ChattyLLM(second=FROM_SECOND_CALL)
    result = turn(tmp_path, llm)
    assert llm.calls == [(QUESTION, ANSWER)], "the second call is handed the final answer"
    assert result.follow_up_questions == FROM_SECOND_CALL
    assert "suggestions" in result.timings_ms
    assert result.timings_ms["total"] >= result.timings_ms["suggestions"]


def test_the_second_call_runs_while_the_answer_is_being_spoken(tmp_path):
    """The whole point: the bubbles must not cost time the turn was not already spending.

    Playback opens the gate; a call that had been made after playback finished
    could never see it open, so an empty result here means the work was
    serialised behind the speech.
    """
    speaking = threading.Event()

    class OverlappedLLM(ChattyLLM):
        def suggest(self, question, answer):
            self.calls.append((question, answer))
            return list(FROM_SECOND_CALL) if speaking.wait(5.0) else []

    class GatePlayer(Player):
        def play(self, audio_path):
            speaking.set()
            return self

    llm = OverlappedLLM()
    result = turn(tmp_path, llm, playback=GatePlayer())
    assert result.follow_up_questions == FROM_SECOND_CALL


def test_a_failing_second_call_leaves_the_answer_alone(tmp_path):
    llm = ChattyLLM(error=RuntimeError("no suggestions today"))
    result = turn(tmp_path, llm)
    assert result.response == ANSWER
    assert result.playback_error is None
    assert result.follow_up_questions == []


def test_a_slow_second_call_is_abandoned_rather_than_waited_on(tmp_path, monkeypatch):
    monkeypatch.setattr("sage_lantern.orchestrator.SUGGESTION_JOIN_SECONDS", 0.2)
    llm = ChattyLLM(second=FROM_SECOND_CALL, delay=1.5)
    started = time.monotonic()
    result = turn(tmp_path, llm)
    elapsed = time.monotonic() - started
    assert elapsed < 1.2, "the turn waits for the join bound, never for the request"
    assert result.response == ANSWER
    assert result.follow_up_questions == []


def test_a_photo_answer_is_never_asked_for_suggestions(tmp_path):
    class VisionLLM(ChattyLLM):
        supports_images = True

        def respond(self, query, context, image_path=None):
            return ANSWER

    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xd9")
    llm = VisionLLM(second=FROM_SECOND_CALL)
    result = turn(tmp_path, llm, image_path=photo)
    assert llm.calls == []
    assert result.follow_up_questions == []


def test_an_adapter_without_the_second_call_still_completes(tmp_path):
    class PlainLLM:
        def respond(self, query, context):
            return ANSWER

    result = turn(tmp_path, PlainLLM())
    assert result.response == ANSWER
    assert result.follow_up_questions == []
