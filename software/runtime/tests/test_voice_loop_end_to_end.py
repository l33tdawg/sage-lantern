"""The whole loop, joined: a config turns speech on, a question is answered, and
the answer reaches the speaker.

Every piece of this exists and is tested on its own:

| Piece | Its own file |
| --- | --- |
| the player | `test_audio_playback.py` |
| playback inside the turn | `test_orchestrator_playback.py` |
| the user's choice | `test_spoken_responses.py` |
| the operator's config | `test_audio_config.py` |

What no test did was run them **together** — configure the sound layer, build the
orchestrator the way `cli.py` builds it, ask a question, and check that the answer
both reaches the screen and is spoken. A chain whose links are each tested is not
a tested chain, and this is the acceptance shape for the goal: speak, see the
answer, hear it.

**No audio device is opened and no model is run.** `AplayPlayback` is patched
where `cli.py` looks it up, so the assertions are about what the runtime asked the
speaker to do. This is synthetic evidence and is labelled as such; nothing here
says unit C's speaker works.
"""


from pathlib import Path
from unittest.mock import patch

import pytest

from sage_lantern.config import RuntimeConfig
from sage_lantern.settings import SettingId, playback_decision
from sage_lantern.spoken_responses import SpokenResponsesStore


CONFIG = """
[runtime]
state_log = "state/events.jsonl"
output_dir = "audio"
[stt]
binary = "bin/whisper-cli"
model = "models/whisper.bin"
[llm]
base_url = "http://127.0.0.1:8081"
model = "test-model"
[sage]
base_url = "http://127.0.0.1:8080"
[context]
static_items = ["Guava leaves have opposite arrangement."]
"""

TTS_ON = '\n[tts]\nenabled = true\npython = "venv/bin/python"\nmodel = "models/voice.onnx"\n'
TTS_OFF = '\n[tts]\nenabled = false\n'
AUDIO = ('\n[audio]\nenabled = true\ndevice = "hw:CARD=rockchipes8388,DEV=0"\n'
         'spoken_dir = "{spoken}"\n')

ANSWER = "Based on local context: Guava leaves have opposite arrangement."
QUESTION = "What plant is this?"


class FakeSTT:
    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio_path):
        assert Path(audio_path).exists(), "the turn must be handed an audio file"
        return QUESTION


class FakeLLM:
    def __init__(self, *args, **kwargs):
        pass

    def respond(self, query, context):
        assert query == QUESTION
        return ANSWER


class FakeTTS:
    """Stands in for Piper: writes the file the turn then hands to the player."""

    def __init__(self, *args, **kwargs):
        pass

    def synthesize(self, text, output_path):
        assert text == ANSWER, "the answer, not the question, is what gets spoken"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFFfake")


class RecordingPlayer:
    """Stands in for `AplayPlayback`, recording what it was asked to speak."""

    instances = []

    def __init__(self, device, *, target_rate=None, **kwargs):
        self.device = device
        self.target_rate = target_rate
        self.kwargs = kwargs
        self.played = []
        RecordingPlayer.instances.append(self)

    def play(self, audio_path):
        self.played.append(Path(audio_path))
        return self

    def wait(self):
        return None

    def stop(self):
        return None


@pytest.fixture(autouse=True)
def _reset_instances():
    RecordingPlayer.instances = []
    yield
    RecordingPlayer.instances = []


def load(extra: str, *, spoken: bool | None = None):
    """A real config, parsed by the real loader, in a real directory.

    The directory is the *test's* own `tmp_path`, not a `TemporaryDirectory` that
    closes on return: the loader validates that the paths it resolves exist, and
    the turn then writes into `output_dir`, so the tree has to outlive the load.

    Each call gets its own subtree. A test that loads several configs — the
    drift guard does — would otherwise share one `spoken_dir`, and the second
    write would collide with the first on revision rather than exercising the
    case the test names.
    """
    base = _ROOT[0]
    _SEQ[0] += 1
    root = base / f"run{_SEQ[0]}"
    # Note: `spoken` is deliberately NOT created here. The store creates its own
    # directory at mode 0700 and refuses one with other permissions, so a fixture
    # that pre-creates it at the default 0755 makes the store look unsafe.
    for name in ("state", "audio", "bin", "models", "venv/bin"):
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in ("bin/whisper-cli", "models/whisper.bin", "models/voice.onnx", "venv/bin/python"):
        (root / name).write_bytes(b"")
    audio_in = root / "input.wav"
    audio_in.write_bytes(b"RIFFfake")
    body = (CONFIG + extra).strip()
    directory = root / "spoken"
    if "{spoken}" in body:
        body = body.replace("{spoken}", str(directory))
    path = root / "lantern.toml"
    path.write_text(body, encoding="utf-8")
    config = RuntimeConfig.load(path)
    if spoken is not None:
        SpokenResponsesStore(directory).update(expected_revision=0, spoken_responses=spoken)
    return config, audio_in


# Set per test so every load lands in that test's own directory tree.
_ROOT: list[Path] = []
# Bumped per load so two configs in one test never share a store.
_SEQ: list[int] = [0]


@pytest.fixture(autouse=True)
def _root(tmp_path):
    _ROOT.clear()
    _ROOT.append(tmp_path)
    _SEQ[0] = 0
    yield


def run_turn(config, audio_path):
    """Build the orchestrator the way `cli.py` does, then run one question."""
    from sage_lantern.cli import build_orchestrator

    with patch("sage_lantern.cli.AplayPlayback", RecordingPlayer), \
         patch("sage_lantern.cli.PiperTTS", FakeTTS), \
         patch("sage_lantern.cli.WhisperCppSTT", FakeSTT), \
         patch("sage_lantern.cli.LlamaCppHTTP", FakeLLM):
        orchestrator = build_orchestrator(config)
    # A missing substitution used to announce itself as an unrelated
    # `PermissionError` from a real binary the turn tried to execute. Check the
    # engines that were actually built, so the next gap says what it is.
    assert isinstance(orchestrator.stt, FakeSTT), "a real transcription binary is in the path"
    assert isinstance(orchestrator.llm, FakeLLM), "a real model client is in the path"
    assert orchestrator.tts is None or isinstance(orchestrator.tts, FakeTTS), "real Piper is in the path"
    assert orchestrator.playback is None or isinstance(orchestrator.playback, RecordingPlayer), \
        "a real player is in the path"
    return orchestrator.run_audio(audio_path)


# --- the whole loop, sound on -------------------------------------------

def test_a_question_is_answered_on_screen_and_read_aloud():
    config, audio = load(TTS_ON + AUDIO, spoken=True)
    result = run_turn(config, audio)
    assert result.transcript == QUESTION
    assert result.response == ANSWER
    assert result.playback_error is None, "the turn should not have failed to speak"


def test_the_speaker_is_handed_the_file_synthesis_wrote():
    config, audio = load(TTS_ON + AUDIO, spoken=True)
    result = run_turn(config, audio)
    assert len(RecordingPlayer.instances) == 1, "exactly one player per process"
    assert RecordingPlayer.instances[0].played == [result.audio_path]


def test_the_configured_device_is_the_one_addressed():
    config, audio = load(TTS_ON + AUDIO, spoken=True)
    run_turn(config, audio)
    assert RecordingPlayer.instances[0].device == "hw:CARD=rockchipes8388,DEV=0"


def test_the_turn_records_that_it_spoke():
    config, audio = load(TTS_ON + AUDIO, spoken=True)
    result = run_turn(config, audio)
    assert "playback" in result.timings_ms
    assert result.timings_ms["total"] >= result.timings_ms["playback"]


# --- the same loop with speech off --------------------------------------

def test_without_an_audio_section_the_answer_is_silent_but_complete():
    """The default installation: text on screen, no player, nothing lost."""
    config, audio = load(TTS_ON)
    result = run_turn(config, audio)
    assert result.response == ANSWER
    assert result.audio_path is not None, "the WAV is still produced"
    assert RecordingPlayer.instances == [], "no player may be built"
    assert "playback" not in result.timings_ms


def test_an_audio_section_alone_does_not_speak_when_the_engine_is_off():
    """The sound layer cannot conjure an engine the config disabled."""
    config, audio = load(TTS_OFF + AUDIO, spoken=True)
    result = run_turn(config, audio)
    assert result.response == ANSWER
    assert result.audio_path is None, "no engine means no synthesis and no WAV"
    assert RecordingPlayer.instances == []


# --- the operator path and the shared decision agree --------------------

def test_the_operator_path_agrees_with_the_shared_decision():
    """`cli.py`'s own condition must match `playback_decision`.

    The construction site has its own three-way condition. This pins it to the
    shared decision so the two cannot drift apart — the failure mode that would
    let a user's setting be honoured in one place and ignored in another.
    """
    assert playback_decision(
        engine_enabled=True, device="hw:X", desired={SettingId.TTS: True}).enabled is True
    assert playback_decision(
        engine_enabled=True, device="hw:X", desired=None).enabled is False
    assert playback_decision(
        engine_enabled=False, device="hw:X", desired={SettingId.TTS: True}).enabled is False
    assert playback_decision(
        engine_enabled=True, device=None, desired={SettingId.TTS: True}).enabled is False

    # The construction site speaks exactly when all three hold — and, since the
    # user layer was wired, that now includes a stored yes. `spoken_intent` is the
    # real reader, so this pins the production path rather than a restatement.
    from sage_lantern.cli import spoken_intent

    cases = [
        (load(TTS_ON + AUDIO, spoken=True), True),
        (load(TTS_ON + AUDIO), False),                # operator configured, user never asked
        (load(TTS_ON + AUDIO, spoken=False), False),  # user explicitly off
        (load(TTS_ON), False),                        # no operator routing at all
        (load(TTS_OFF + AUDIO, spoken=True), False),  # user asked, engine disabled
    ]
    for (config, _audio), expected in cases:
        decision = playback_decision(
            engine_enabled=config.tts_enabled,
            device=config.audio_device if config.audio_enabled else None,
            desired=spoken_intent(config),
        )
        assert decision.enabled is expected, (config.audio_device, spoken_intent(config))


# --- the operator's device rate reaches the joined loop -----------------

def test_a_stated_device_rate_reaches_the_player_the_loop_builds():
    """Round 10 proved the conversion works; this proves it is *authorized*.

    A resampler nothing calls is not a working voice loop, so the config key that
    authorizes the conversion is asserted through the real `build_orchestrator`
    rather than in the config tests alone.
    """
    config, _audio = load(TTS_ON + AUDIO + "target_rate = 16000\n", spoken=True)
    assert config.audio_target_rate == 16000
    recording = run_turn(config, _audio)
    assert recording is not None
    player = RecordingPlayer.instances[-1]
    assert player.target_rate == 16000, "the device's rate must reach the player"
    assert player.device == config.audio_device


def test_an_unmeasured_device_is_not_converted_for():
    """No stated rate means a mismatch is still refused, not silently fixed."""
    config, _audio = load(TTS_ON + AUDIO, spoken=True)
    run_turn(config, _audio)
    assert RecordingPlayer.instances[-1].target_rate is None


def test_converting_does_not_change_what_is_shown_on_screen():
    """The answer text is independent of whether the speaker needed a conversion."""
    plain, audio_a = load(TTS_ON + AUDIO, spoken=True)
    converted, audio_b = load(TTS_ON + AUDIO + "target_rate = 16000\n", spoken=True)
    first = run_turn(plain, audio_a)
    second = run_turn(converted, audio_b)
    assert first.response == second.response
    assert first.transcript == second.transcript
    assert first.playback_error is None and second.playback_error is None
