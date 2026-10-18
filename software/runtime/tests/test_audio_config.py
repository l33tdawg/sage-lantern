"""The operator layer for spoken responses: `[audio]` in lantern.toml.

`tts.enabled` decides whether the synthesis engine exists. `[audio]` decides
whether its output has somewhere to go. Both default off, so an installation that
has never heard of either key ends the turn at a WAV exactly as it did before
playback existed — that default is the thing most worth pinning here.

No audio device is opened; the construction test patches the player.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sage_lantern.config import RuntimeConfig
from sage_lantern.spoken_responses import SpokenResponsesStore


BASE = """
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
static_items = ["offline fixture"]
"""

# A TTS section, supplied per test. It is not in BASE because two tests need
# different values for `enabled`, and a second header for a declared table is a
# TOML error rather than an override.
TTS_ON = '\n[tts]\nenabled = true\npython = "venv/bin/python"\nmodel = "models/voice.onnx"\n'
TTS_OFF = '\n[tts]\nenabled = false\n'


def load(extra: str = "", *, tmp_path=None, spoken: bool | None = None):
    """Parse a real config from a real file.

    `spoken_dir` is substituted from the caller's own `tmp_path`, and when
    `spoken` is given the preference is written through the real store. That
    ordering matters: since the user layer was wired, the operator keys alone no
    longer start a player, so a test that wants a player has to configure the
    user's choice too — exactly as a real installation does.

    The directory is the caller's, not a `TemporaryDirectory` created here. A
    helper that returns paths from a directory it then deletes produces a config
    whose validated paths have already gone.
    """
    root = Path(tmp_path) if tmp_path is not None else Path(tempfile.mkdtemp())
    root.mkdir(parents=True, exist_ok=True)
    body = (BASE + extra).strip()
    directory = None
    if "{spoken}" in body:
        directory = root / "spoken"
        body = body.replace("{spoken}", str(directory))
    path = root / "lantern.toml"
    path.write_text(body, encoding="utf-8")
    config = RuntimeConfig.load(path)
    if spoken is not None:
        assert directory is not None, "ask for a stored preference only with a [spoken] placeholder"
        SpokenResponsesStore(directory).update(expected_revision=0, spoken_responses=spoken)
    return config


# --- defaults: nothing changes for an installation that ignores this -----

def test_audio_is_off_and_deviceless_when_the_section_is_absent():
    config = load(TTS_ON)
    assert config.audio_enabled is False
    assert config.audio_device is None


def test_the_engine_default_is_unchanged_by_this_work():
    """`tts.enabled` still defaults true; the sound layer defaults false."""
    assert load(TTS_ON).tts_enabled is True


def test_a_configured_device_alone_does_not_enable_playback():
    config = load(TTS_ON + '[audio]\ndevice = "hw:CARD=rockchipes8388,DEV=0"\n')
    assert config.audio_device == "hw:CARD=rockchipes8388,DEV=0"
    assert config.audio_enabled is False


def test_enabling_with_a_device_records_both():
    config = load(TTS_ON + '[audio]\nenabled = true\ndevice = "hw:CARD=rockchipes8388,DEV=0"\n')
    assert config.audio_enabled is True
    assert config.audio_device == "hw:CARD=rockchipes8388,DEV=0"


# --- mistakes are caught at start-up, not per turn ----------------------

def test_enabled_without_a_device_is_refused_at_load():
    """Otherwise a player starts with nowhere to send sound, once per answer."""
    with pytest.raises(ValueError, match="audio.device is required"):
        load("\n[audio]\nenabled = true\n")


@pytest.mark.parametrize("device", ['""', '"   "', '"\\t"'])
def test_a_blank_device_is_refused(device):
    with pytest.raises(ValueError, match="audio.device must be a short non-empty"):
        load(f"\n[audio]\ndevice = {device}\n")


def test_a_non_string_device_is_refused():
    with pytest.raises(ValueError, match="audio.device must be a short non-empty"):
        load("\n[audio]\ndevice = 7\n")


def test_an_overlong_device_is_refused():
    with pytest.raises(ValueError, match="audio.device must be a short non-empty"):
        load('\n[audio]\ndevice = "' + "x" * 257 + '"\n')


@pytest.mark.parametrize("value", ['"yes"', "1", '"true"'])
def test_a_non_boolean_enabled_is_refused_rather_than_coerced(value):
    with pytest.raises(ValueError, match="audio.enabled must be a boolean"):
        load(f"\n[audio]\nenabled = {value}\n")


# --- the construction site actually uses it -----------------------------

def test_the_orchestrator_is_built_with_a_player_only_when_asked(tmp_path):
    """Now that the user layer is wired, ALL of it must hold.

    Operator keys alone are no longer enough: the stored preference has to say
    yes as well, which is the two-layer ruling the config keys and the setting
    each own half of.
    """
    from sage_lantern.cli import build_orchestrator

    spoken = '[audio]\nenabled = true\ndevice = "hw:CARD=rockchipes8388,DEV=0"\nspoken_dir = "{spoken}"\n'
    # Operator configured, but no stored preference yet: silent.
    unasked = load(TTS_ON + spoken, tmp_path=tmp_path / "unasked")
    # Operator configured AND the user asked: speaks.
    asked = load(TTS_ON + spoken, tmp_path=tmp_path / "asked", spoken=True)

    with patch("sage_lantern.cli.AplayPlayback") as player, patch("sage_lantern.cli.PiperTTS"):
        build_orchestrator(unasked)
        assert player.called is False, "an unasked device must not get a player"
        player.reset_mock()
        build_orchestrator(asked)
        assert player.called is True, "a configured device with a stored yes must get one"
        # No audio.target_rate in this fixture, so no conversion is authorized:
        # the player is built for the device and told nothing about its rate.
        player.assert_called_once_with("hw:CARD=rockchipes8388,DEV=0", target_rate=None)


def test_a_stored_no_keeps_the_speaker_silent_despite_the_operator_keys(tmp_path):
    """The user's off must beat the operator's configuration."""
    from sage_lantern.cli import build_orchestrator

    config = load(
        TTS_ON + '[audio]\nenabled = true\ndevice = "hw:X,DEV=0"\nspoken_dir = "{spoken}"\n',
        tmp_path=tmp_path, spoken=False)
    with patch("sage_lantern.cli.AplayPlayback") as player, patch("sage_lantern.cli.PiperTTS"):
        build_orchestrator(config)
    assert player.called is False


def test_a_disabled_engine_means_no_player_even_when_the_user_asked(tmp_path):
    """The sound layer cannot conjure an engine the config disabled."""
    from sage_lantern.cli import build_orchestrator

    config = load(
        TTS_OFF + '[audio]\nenabled = true\ndevice = "hw:X,DEV=0"\nspoken_dir = "{spoken}"\n',
        tmp_path=tmp_path, spoken=True)
    with patch("sage_lantern.cli.AplayPlayback") as player, patch("sage_lantern.cli.PiperTTS"):
        build_orchestrator(config)
    assert player.called is False


def test_an_unreadable_store_is_silent_rather_than_fatal(tmp_path):
    """A settings fault must not take the screen answer down with the speaker."""
    from sage_lantern.cli import build_orchestrator, spoken_intent

    config = load(
        TTS_ON + '[audio]\nenabled = true\ndevice = "hw:X,DEV=0"\nspoken_dir = "{spoken}"\n',
        tmp_path=tmp_path)
    config.spoken_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    broken = config.spoken_dir / "spoken-responses.json"
    broken.write_text("not json", encoding="utf-8")
    broken.chmod(0o600)
    assert spoken_intent(config) is None, "an unreadable store reads as no user layer"
    with patch("sage_lantern.cli.AplayPlayback") as player, patch("sage_lantern.cli.PiperTTS"):
        build_orchestrator(config)  # must not raise
    assert player.called is False


def test_no_configured_directory_means_no_user_layer_at_all(tmp_path):
    """`None` (no store) and `False` (a user said no) are different inputs."""
    from sage_lantern.cli import spoken_intent

    assert spoken_intent(load(TTS_ON + '[audio]\nenabled = true\ndevice = "hw:X,DEV=0"\n',
                              tmp_path=tmp_path)) is None


# --- audio.target_rate: the operator's statement about the device ------

def test_a_stated_target_rate_reaches_the_player(tmp_path):
    """The config must actually authorize the conversion, not merely allow it."""
    from sage_lantern.cli import build_orchestrator

    body = TTS_ON + ('[audio]\nenabled = true\ndevice = "hw:CARD=rockchipes8388,DEV=0"\n'
                     'spoken_dir = "{spoken}"\ntarget_rate = 16000\n')
    config = load(body, tmp_path=tmp_path, spoken=True)
    assert config.audio_target_rate == 16000
    with patch("sage_lantern.cli.AplayPlayback") as player, patch("sage_lantern.cli.PiperTTS"):
        build_orchestrator(config)
        player.assert_called_once_with(config.audio_device, target_rate=16000)


def test_no_stated_rate_means_no_conversion_is_authorized(tmp_path):
    """Absent is not 16000: an unmeasured device must not be converted for."""
    body = TTS_ON + ('[audio]\nenabled = true\ndevice = "hw:CARD=rockchipes8388,DEV=0"\n'
                     'spoken_dir = "{spoken}"\n')
    config = load(body, tmp_path=tmp_path, spoken=True)
    assert config.audio_target_rate is None


@pytest.mark.parametrize("bad", ["0", "-1", "7999", "192001", "16000.5", '"16000"', "true"])
def test_a_nonsense_target_rate_is_a_load_error(tmp_path, bad):
    """`true` is included: bool is an int subclass and must still be refused."""
    body = TTS_ON + ('[audio]\nenabled = true\ndevice = "hw:CARD=rockchipes8388,DEV=0"\n'
                     f'target_rate = {bad}\n')
    with pytest.raises(ValueError):
        load(body, tmp_path=tmp_path)
