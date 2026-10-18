"""Playback inside the voice turn: does the answer actually get spoken?

`AplayPlayback` is tested on its own in `test_audio_playback.py`. This file covers
the other half — the orchestrator invoking it, in the right order, without a
speaker failure being able to destroy an answer that was produced successfully.

No audio device is opened: the playback fake records calls.
"""

import pytest

from sage_lantern.orchestrator import VoiceOrchestrator
from sage_lantern.state import JsonlEventLog, StateMachine


class FakeSTT:
    def transcribe(self, audio_path):
        return "What plant is this?"


class FakeContext:
    def recall(self, query):
        return ["Guava leaves have opposite arrangement."]


class FakeLLM:
    def respond(self, query, context):
        return "Based on local context: Guava leaves have opposite arrangement."


class FakeTTS:
    """Records that synthesis happened, and writes a file like the real one."""

    def __init__(self, events):
        self.events = events

    def synthesize(self, text, output_path):
        self.events.append("synthesize")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFFfake")


class FakeHandle:
    def __init__(self, events, error=None):
        self.events = events
        self.error = error
        self.stopped = False

    def wait(self):
        self.events.append("wait")
        if self.error is not None:
            raise self.error

    def stop(self):
        self.stopped = True


class FakePlayback:
    """Records what it was asked to play, and when."""

    def __init__(self, events, error=None):
        self.events = events
        self.error = error
        self.played = []
        self.handles = []

    def play(self, audio_path):
        self.events.append("play")
        self.played.append(audio_path)
        handle = FakeHandle(self.events, self.error)
        self.handles.append(handle)
        return handle


def build(tmp_path, tts, playback=None):
    return VoiceOrchestrator(
        StateMachine(JsonlEventLog(tmp_path / "state.jsonl")),
        FakeSTT(), FakeContext(), FakeLLM(), tts, tmp_path,
        playback=playback,
    )


def test_a_synthesized_answer_is_played_before_the_turn_ends(tmp_path):
    events = []
    playback = FakePlayback(events)
    orchestrator = build(tmp_path, FakeTTS(events), playback)
    result = orchestrator.run_audio(tmp_path / "fixture.wav")
    # Order is the point: the WAV must exist before anything tries to speak it,
    # and the turn must not end while sound is still being produced.
    assert events == ["synthesize", "play", "wait"]
    assert result.playback_error is None


def test_the_file_played_is_the_file_synthesis_wrote(tmp_path):
    events = []
    playback = FakePlayback(events)
    orchestrator = build(tmp_path, FakeTTS(events), playback)
    result = orchestrator.run_audio(tmp_path / "fixture.wav")
    assert playback.played == [result.audio_path]


def test_playback_time_is_recorded_separately_from_synthesis(tmp_path):
    events = []
    orchestrator = build(tmp_path, FakeTTS(events), FakePlayback(events))
    result = orchestrator.run_audio(tmp_path / "fixture.wav")
    assert "tts" in result.timings_ms
    assert "playback" in result.timings_ms
    # `total` must account for both, or the turn looks faster than it was.
    assert result.timings_ms["total"] >= result.timings_ms["tts"] + result.timings_ms["playback"]


def test_without_a_playback_adapter_nothing_changes(tmp_path):
    """The default must leave every existing installation byte-for-byte as it was."""
    events = []
    orchestrator = build(tmp_path, FakeTTS(events), None)
    result = orchestrator.run_audio(tmp_path / "fixture.wav")
    assert events == ["synthesize"]
    assert result.playback_error is None
    assert "playback" not in result.timings_ms
    assert result.audio_path is not None


def test_with_no_tts_there_is_nothing_to_play(tmp_path):
    events = []
    playback = FakePlayback(events)
    orchestrator = build(tmp_path, None, playback)
    result = orchestrator.run_audio(tmp_path / "fixture.wav")
    assert playback.played == []
    assert events == []
    assert result.audio_path is None


# --- a speaker failure must not destroy the answer ----------------------

def test_a_failed_speaker_does_not_lose_the_answer(tmp_path):
    events = []
    playback = FakePlayback(events, error=RuntimeError("aplay failed (1): no such device"))
    orchestrator = build(tmp_path, FakeTTS(events), playback)
    result = orchestrator.run_audio(tmp_path / "fixture.wav")
    assert result.response == "Based on local context: Guava leaves have opposite arrangement."
    assert result.transcript == "What plant is this?"


def test_a_failed_speaker_is_reported_rather_than_swallowed(tmp_path):
    events = []
    playback = FakePlayback(events, error=RuntimeError("aplay failed (1): no such device"))
    orchestrator = build(tmp_path, FakeTTS(events), playback)
    result = orchestrator.run_audio(tmp_path / "fixture.wav")
    assert result.playback_error is not None
    assert "no such device" in result.playback_error


def test_a_failed_speaker_leaves_the_wav_for_the_reader(tmp_path):
    """The text and its file survive; only the sound did not happen."""
    events = []
    playback = FakePlayback(events, error=RuntimeError("device busy"))
    orchestrator = build(tmp_path, FakeTTS(events), playback)
    result = orchestrator.run_audio(tmp_path / "fixture.wav")
    assert result.audio_path is not None
    assert result.audio_path.is_file()


def test_the_turn_does_not_end_in_an_error_state_when_speaking_fails(tmp_path):
    events = []
    playback = FakePlayback(events, error=RuntimeError("device busy"))
    orchestrator = build(tmp_path, FakeTTS(events), playback)
    orchestrator.run_audio(tmp_path / "fixture.wav")
    transitions = (tmp_path / "state.jsonl").read_text().strip().splitlines()
    assert transitions, "the turn must still record its transitions"
    assert "ERROR" not in transitions[-1]


@pytest.mark.parametrize("error", [RuntimeError("x"), OSError("x"), ValueError("x")])
def test_any_player_failure_is_contained(tmp_path, error):
    """Matched broadly on purpose: no speaker fault is a reason to lose the text."""
    events = []
    orchestrator = build(tmp_path, FakeTTS(events), FakePlayback(events, error=error))
    result = orchestrator.run_audio(tmp_path / "fixture.wav")
    assert result.response
    assert result.playback_error is not None
