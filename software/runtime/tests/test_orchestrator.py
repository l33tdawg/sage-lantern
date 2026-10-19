import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from sage_lantern.orchestrator import VoiceOrchestrator
from sage_lantern.state import JsonlEventLog, RuntimeState, StateMachine


class FakeSTT:
    def transcribe(self, audio_path):
        self.audio_path = audio_path
        return "What plant is this?"


class FakeContext:
    def recall(self, query):
        return ["Guava leaves have opposite arrangement."]


class FakeLLM:
    def respond(self, query, context):
        return f"Based on local context: {context[0]}"


def test_result_carries_only_numeric_inference_metrics(tmp_path):
    model = FakeLLM()
    model.last_metrics = {'prompt_tokens': 42, 'output_tokens': True, 'text': 'PRIVATE'}
    orchestrator = VoiceOrchestrator(StateMachine(JsonlEventLog(tmp_path / 'state.jsonl')),
        FakeSTT(), FakeContext(), model, None, tmp_path)
    result = orchestrator.run_audio(tmp_path / 'fixture.wav')
    assert result.inference_metrics['prompt_tokens'] == 42
    assert result.inference_metrics['output_tokens'] is None
    assert 'PRIVATE' not in json.dumps(result.inference_metrics)


class FakeTTS:
    def synthesize(self, text, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFFfake")


class StubVerifier:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = []

    def verify(self, question, answer):
        self.calls.append((question, answer))
        return self.verdict


@pytest.mark.parametrize("status,text", [("ok", "Based on local context: Guava leaves have opposite arrangement."),
                                        ("corrected", "Guava leaves are opposite."),
                                        ("unavailable", "Based on local context: Guava leaves have opposite arrangement.")])
def test_verifier_replaces_only_a_corrected_answer(tmp_path, status, text):
    from sage_lantern.answer_verification import Verdict

    verifier = StubVerifier(Verdict(status, text, 0.4))
    orchestrator = VoiceOrchestrator(StateMachine(JsonlEventLog(tmp_path / 'state.jsonl')),
        FakeSTT(), FakeContext(), FakeLLM(), None, tmp_path, verifier)
    result = orchestrator.run_audio(tmp_path / 'fixture.wav')
    assert result.response == text
    assert result.verification["status"] == status
    assert result.timings_ms["verify"] == pytest.approx(400, abs=1)
    assert verifier.calls == [("What plant is this?",
                               "Based on local context: Guava leaves have opposite arrangement.")]


def test_verifier_is_not_consulted_without_a_response(tmp_path):
    from sage_lantern.answer_verification import Verdict

    class SilentLLM:
        last_metrics = None

        def respond(self, query, context):
            return "   "

    verifier = StubVerifier(Verdict("corrected", "never", 0.1))
    orchestrator = VoiceOrchestrator(StateMachine(JsonlEventLog(tmp_path / 'state.jsonl')),
        FakeSTT(), FakeContext(), SilentLLM(), None, tmp_path, verifier)
    result = orchestrator.run_audio(tmp_path / 'fixture.wav')
    assert result.response == "   " and result.verification is None and verifier.calls == []


class OrchestratorTests(unittest.TestCase):
    def test_complete_voice_loop_uses_one_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "events.jsonl"
            state = StateMachine(JsonlEventLog(log_path))
            orchestrator = VoiceOrchestrator(
                state,
                FakeSTT(),
                FakeContext(),
                FakeLLM(),
                FakeTTS(),
                root / "audio",
            )
            result = orchestrator.run_audio(root / "input.wav", session_id="session-1")
            self.assertEqual(state.state, RuntimeState.IDLE)
            self.assertTrue(result.audio_path.is_file())
            events = [json.loads(line) for line in log_path.read_text().splitlines()]
            self.assertEqual({event["session_id"] for event in events}, {"session-1"})
            self.assertEqual(
                [event["state"] for event in events],
                ["listening", "transcribing", "thinking", "speaking", "idle"],
            )

    def test_screen_only_loop_skips_tts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "events.jsonl"
            state = StateMachine(JsonlEventLog(log_path))
            orchestrator = VoiceOrchestrator(
                state, FakeSTT(), FakeContext(), FakeLLM(), None, root / "audio"
            )
            result = orchestrator.run_audio(root / "input.wav", session_id="screen-only")
            self.assertIsNone(result.audio_path)
            self.assertNotIn("tts", result.timings_ms)
            events = [json.loads(line) for line in log_path.read_text().splitlines()]
            self.assertEqual(
                [event["state"] for event in events],
                ["listening", "transcribing", "thinking", "idle"],
            )


def test_stream_timings_transcript_and_callback_backpressure(tmp_path):
    clock = [0.0]
    emitted = []

    class TimedSTT:
        def transcribe(self, path):
            clock[0] += 2
            return "question"

    class TimedContext:
        def recall(self, query):
            clock[0] += 1
            return ["context"]

    class StreamingLLM:
        def respond(self, query, context):
            raise AssertionError("non-streaming path called")

        def respond_stream(self, query, context, on_delta):
            assert query == "question" and context == ["context"]
            clock[0] += 0.5
            on_delta("hello ")
            clock[0] += 1
            on_delta("world")
            clock[0] += 0.5
            return "hello world"

    def callback(text, transcript):
        emitted.append((text, transcript))
        clock[0] += 0.25

    state = StateMachine(JsonlEventLog(tmp_path / "events.jsonl"))
    runner = VoiceOrchestrator(state, TimedSTT(), TimedContext(), StreamingLLM(), None, tmp_path)
    with patch("sage_lantern.orchestrator.time.monotonic", side_effect=lambda: clock[0]):
        result = runner.run_audio(tmp_path / "input.wav", "session", on_delta=callback)
    assert emitted == [("hello ", "question"), ("world", "question")]
    assert result.response == "hello world"
    assert result.timings_ms == {
        "stt": 2000, "sage_context": 1000, "llm": 2500,
        "llm_first_token": 500, "first_text": 3500, "delta_callback": 500, "total": 5500,
    }
    assert state.state == RuntimeState.IDLE


def test_events_fallback_does_not_fabricate_deltas_or_ttft(tmp_path):
    state = StateMachine(JsonlEventLog(tmp_path / "events.jsonl"))
    emitted = []
    runner = VoiceOrchestrator(state, FakeSTT(), FakeContext(), FakeLLM(), None, tmp_path)
    result = runner.run_audio(tmp_path / "input.wav", on_delta=lambda *delta: emitted.append(delta))
    assert result.response
    assert emitted == []
    assert set(result.timings_ms) == {"stt", "sage_context", "llm", "total"}


def test_default_does_not_use_optional_streaming_method(tmp_path):
    class StreamingLLM(FakeLLM):
        def respond_stream(self, *args):
            raise AssertionError("streaming must be opt-in")

    state = StateMachine(JsonlEventLog(tmp_path / "events.jsonl"))
    result = VoiceOrchestrator(state, FakeSTT(), FakeContext(), StreamingLLM(), FakeTTS(), tmp_path).run_audio(tmp_path / "input.wav")
    assert result.audio_path.is_file()
    assert set(result.timings_ms) == {"stt", "sage_context", "llm", "tts", "total"}
    assert result.timings_ms["total"] == sum(result.timings_ms[stage] for stage in ("stt", "sage_context", "llm", "tts"))


def test_stream_failure_does_not_record_completed_or_run_tts(tmp_path):
    class FailingLLM(FakeLLM):
        def respond_stream(self, query, context, callback):
            callback("partial")
            raise RuntimeError("incomplete stream")

    state = StateMachine(JsonlEventLog(tmp_path / "events.jsonl"))
    runner = VoiceOrchestrator(state, FakeSTT(), FakeContext(), FailingLLM(), FakeTTS(), tmp_path)
    with pytest.raises(RuntimeError, match="incomplete"):
        runner.run_audio(tmp_path / "input.wav", on_delta=lambda *args: None)
    assert state.state == RuntimeState.ERROR
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [event["state"] for event in events] == ["listening", "transcribing", "thinking", "error"]
    assert not list(tmp_path.glob("*.wav"))


@pytest.mark.parametrize("photo", [False, True])
def test_progress_precedes_actual_calls_and_first_visible_delta(tmp_path, photo):
    events = []

    class STT:
        def transcribe(self, path):
            events.append("stt_call")
            return "question"

    class Context:
        def recall(self, query):
            events.append("recall_call")
            return []

    class Model:
        supports_images = True

        def respond_stream(self, query, context, callback, **options):
            assert options == ({"image_path": tmp_path / "photo.jpg"} if photo else {})
            events.append("llm_call")
            for text in ("", " \n", "hello", "world"):
                callback(text)
            return "hello world"

    class TTS:
        def synthesize(self, text, path):
            events.append("tts_call")

    runner = VoiceOrchestrator(StateMachine(JsonlEventLog(tmp_path / "events.jsonl")),
                               STT(), Context(), Model(), TTS(), tmp_path)
    runner.run_audio(tmp_path / "input.wav", on_delta=lambda text, transcript: events.append("delta:" + text),
                     image_path=tmp_path / "photo.jpg" if photo else None, on_progress=events.append)
    assert events == ["transcribing", "stt_call", "recalling", "recall_call",
                      "photo_analysis" if photo else "generating", "llm_call", "delta: \n",
                      "answering", "delta:hello", "delta:world", "speaking", "tts_call"]


def test_progress_stops_after_actual_failure(tmp_path):
    class FailedSTT:
        def transcribe(self, path):
            raise RuntimeError("failed")

    events = []
    state = StateMachine(JsonlEventLog(tmp_path / "events.jsonl"))
    runner = VoiceOrchestrator(state, FailedSTT(), FakeContext(), FakeLLM(), FakeTTS(), tmp_path)
    with pytest.raises(RuntimeError, match="failed"):
        runner.run_audio(tmp_path / "input.wav", on_progress=events.append)
    assert events == ["transcribing"]
    assert state.state == RuntimeState.ERROR


if __name__ == "__main__":
    unittest.main()
