"""The consumer for the spoken-responses setting.

`settings.py` projects state read-only and the draft store persists what the user
asked for, but neither decides whether sound leaves the speaker. This covers that
decision, which is the piece that makes `SettingId.TTS` real rather than recorded.

Pure policy: no audio device, no store, no process.
"""

from pathlib import Path

import pytest

from sage_lantern.settings import (
    Implementation,
    PlaybackUnavailable,
    REGISTRY,
    SettingId,
    SettingKind,
    playback_decision,
    spoken_responses_enabled,
)


# --- off unless the user said so ----------------------------------------

def test_nothing_asked_for_means_off():
    assert spoken_responses_enabled(None) is False
    assert spoken_responses_enabled({}) is False


def test_an_untouched_setting_is_off_even_when_other_settings_are_set():
    """The unit has a voice and a photo preference; neither speaks for TTS."""
    assert spoken_responses_enabled({SettingId.VOICE: True, SettingId.PHOTO: True}) is False


def test_explicitly_off_is_off():
    assert spoken_responses_enabled({SettingId.TTS: False}) is False


def test_explicitly_on_is_on():
    assert spoken_responses_enabled({SettingId.TTS: True}) is True


def test_on_alongside_other_settings_is_still_on():
    assert spoken_responses_enabled({SettingId.VOICE: True, SettingId.TTS: True}) is True


# --- truthiness is not consent ------------------------------------------

@pytest.mark.parametrize("value", [1, 0, "true", "on", "yes", [], [1], {}, 1.0])
def test_a_non_boolean_is_refused_rather_than_coerced(value):
    """A truthy string must not be how an unexplained speaking device happens."""
    with pytest.raises(ValueError, match="boolean"):
        spoken_responses_enabled({SettingId.TTS: value})


def test_the_refusal_is_for_any_setting_not_only_tts():
    with pytest.raises(ValueError, match="boolean"):
        spoken_responses_enabled({SettingId.VOICE: "yes"})


# --- a typo is loud, not a silent disable -------------------------------

@pytest.mark.parametrize("value", ["spoken_response", "TTS", "", "tts"])
def test_an_unknown_setting_identifier_raises(value):
    """Silently ignoring a misspelled key would disable a feature without a word."""
    with pytest.raises(ValueError, match="unknown setting"):
        spoken_responses_enabled({value: True})


def test_an_action_kind_cannot_be_read_as_a_boolean_setting():
    """Recovery is an ACTION; treating it as a switch is a category error."""
    assert REGISTRY[SettingId.RECOVERY].kind is SettingKind.ACTION
    with pytest.raises(ValueError, match="actions cannot carry"):
        spoken_responses_enabled({SettingId.RECOVERY: True})


def test_a_non_mapping_is_refused():
    for value in (True, "on", 1, [SettingId.TTS]):
        with pytest.raises(ValueError, match="mapping"):
            spoken_responses_enabled(value)


# --- the setting this reads is the one the contract names ---------------

def test_the_setting_is_the_declared_capability_the_contract_names():
    definition = REGISTRY[SettingId.TTS]
    assert definition.setting_id.value == "spoken_responses"
    assert definition.kind is SettingKind.CAPABILITY
    assert definition.implementation is Implementation.EXISTING


# --- the two layers, decided rather than left ambiguous -----------------

def test_config_enables_the_engine_and_the_setting_enables_the_sound():
    """Two switches, two layers. The owner decided this on 10 October 2026.

    Recorded here because the two defaults point in OPPOSITE directions and
    neither module knows about the other, so the layering is the only thing that
    makes them coherent:

      * **The engine layer.** `config.py:82` parses `tts.enabled` with
        `bool(tts.get("enabled", True))`. This decides whether Piper is
        constructed at all — whether the answer *can* be synthesized. On an
        installation whose `lantern.toml` omits the key, it is on.
      * **The sound layer.** `spoken_responses_enabled(None)` is `False`. This
        decides whether the user hears it, and an untouched setting means no.

    So synthesis may run while nothing plays, and that is correct rather than a
    contradiction: a WAV on disk with no speaker is exactly the state of an
    installation whose user has not asked to be read to. The reverse must never
    hold — the sound layer cannot conjure an engine that config disabled — which
    is why an apply handler has to respect `tts.enabled` rather than replace it.

    The assertions below pin the layering rather than a behaviour, because the
    behaviour belongs to the apply handler that is not wired yet. If either
    default moves, this fails and the layering has to be re-decided.
    """
    # The sound layer defaults to silence.
    assert spoken_responses_enabled(None) is False

    root = Path(__file__).resolve().parents[1] / "src/sage_lantern"
    config_source = (root / "config.py").read_text()
    settings_source = (root / "settings.py").read_text()

    # The engine layer still defaults to synthesizing.
    assert 'tts.get("enabled", True)' in config_source, "config default changed; re-read the layering above"
    # Neither module reaches into the other's decision: the layering is held by
    # the caller, which is what keeps the two switches from silently fighting.
    assert "tts_enabled" not in settings_source
    assert "spoken_responses_enabled" not in config_source


# --- the composition: all three must hold -------------------------------

def decide(**overrides):
    arguments = {
        "engine_enabled": True,
        "device": "hw:CARD=rockchipes8388,DEV=0",
        "desired": {SettingId.TTS: True},
    }
    arguments.update(overrides)
    return playback_decision(**arguments)


def test_all_three_holding_means_speak():
    decision = decide()
    assert decision.enabled is True
    assert decision.reason is None


def test_asking_to_be_read_to_is_not_enough_without_an_engine():
    decision = decide(engine_enabled=False)
    assert decision.enabled is False
    assert decision.reason is PlaybackUnavailable.ENGINE_DISABLED


def test_asking_to_be_read_to_is_not_enough_without_a_device():
    decision = decide(device=None)
    assert decision.enabled is False
    assert decision.reason is PlaybackUnavailable.NO_DEVICE


@pytest.mark.parametrize("device", ["", "   ", "\t", "\n"])
def test_a_blank_device_is_no_device_and_is_never_defaulted(device):
    """Guessing an ALSA device is how a device speaks out of the wrong output."""
    decision = decide(device=device)
    assert decision.enabled is False
    assert decision.reason is PlaybackUnavailable.NO_DEVICE


def test_an_engine_and_a_device_do_not_speak_for_an_unasked_user():
    """The hardware being ready is not consent."""
    decision = decide(desired=None)
    assert decision.enabled is False
    assert decision.reason is None


def test_an_explicit_off_reports_no_reason_because_none_is_owed():
    """A user who said no is not owed an explanation about hardware."""
    decision = decide(desired={SettingId.TTS: False})
    assert decision.enabled is False
    assert decision.reason is None


def test_intent_is_checked_before_hardware():
    """With neither intent nor a device, the answer is silence without a lecture."""
    assert decide(desired=None, device=None, engine_enabled=False).reason is None


def test_a_user_who_asked_is_told_the_real_reason_for_silence():
    """The point of carrying a reason: 'I asked and nothing happened' is explicable."""
    assert decide(engine_enabled=False).reason is PlaybackUnavailable.ENGINE_DISABLED
    assert decide(device=None).reason is PlaybackUnavailable.NO_DEVICE


def test_the_engine_layer_cannot_be_overridden_by_a_setting():
    """A config that disabled synthesis stays disabled however the user voted."""
    for desired in ({SettingId.TTS: True}, {SettingId.TTS: False}, None):
        assert decide(engine_enabled=False, desired=desired).enabled is False


def test_a_malformed_setting_still_raises_through_the_composition():
    """The composition must not soften the resolver's loud failure into silence."""
    with pytest.raises(ValueError, match="boolean"):
        decide(desired={SettingId.TTS: "on"})
    with pytest.raises(ValueError, match="unknown setting"):
        decide(desired={"spoken_response": True})
