"""The playback half of the voice loop: does the answer reach the speaker?

`PiperTTS` was already tested; what had no test — and no implementation — was
anything that plays the file it writes. These tests cover the invocation, the
format refusal, and the two failure modes that matter for a speaker: a player
that failed, and a stop that was requested while sound was already coming out.

No real audio device is opened here. `subprocess.Popen` is patched throughout,
so this measures the adapter's behaviour, not a speaker.
"""

import subprocess
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from sage_lantern.adapters import AplayPlayback


def write_wav(path: Path, *, rate=16000, channels=2, width=2, frames=160) -> Path:
    """A real WAV file of the requested shape, so `wave.open` reads it for real."""
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(rate)
        stream.writeframes(b"\x00" * (frames * channels * width))
    return path


class FakeProcess:
    """A stand-in for `subprocess.Popen` that records how it was treated."""

    def __init__(self, *, returncode=0, stderr=b"", running=True):
        self.returncode = returncode
        self._stderr = stderr
        self._running = running
        self.terminated = False
        self.killed = False
        self.waited = 0

    def poll(self):
        return None if self._running else self.returncode

    def communicate(self, *args, **kwargs):
        self._running = False
        return (b"", self._stderr)

    def wait(self, timeout=None):
        self.waited += 1
        self._running = False
        if self.terminated or self.killed:
            self.returncode = -15
        return self.returncode

    def terminate(self):
        self.terminated = True
        self._running = False

    def kill(self):
        self.killed = True
        self._running = False


# --- the format is checked, not assumed ---------------------------------

def test_a_wav_matching_the_device_path_is_played(tmp_path):
    audio = write_wav(tmp_path / "answer.wav", rate=16000, channels=2, width=2)
    process = FakeProcess()
    with patch("subprocess.Popen", return_value=process) as popen:
        handle = AplayPlayback("hw:CARD=rockchipes8388,DEV=0").play(audio)
        handle.wait()
    command = popen.call_args[0][0]
    assert command[0] == "/usr/bin/aplay"
    assert "-D" in command and "hw:CARD=rockchipes8388,DEV=0" in command
    assert str(audio) in command


def test_pipers_default_rate_is_refused_rather_than_silently_resampled(tmp_path):
    """22050 Hz is what `en_US-lessac-medium` actually produces."""
    audio = write_wav(tmp_path / "lessac.wav", rate=22050, channels=1, width=2)
    with patch("subprocess.Popen") as popen, pytest.raises(RuntimeError, match="does not match the device path"):
        AplayPlayback("hw:CARD=rockchipes8388,DEV=0").play(audio)
    assert not popen.called, "a refused file must not reach the speaker"


def test_the_refusal_names_both_the_file_and_the_expected_path(tmp_path):
    audio = write_wav(tmp_path / "lessac.wav", rate=22050, channels=1, width=2)
    with pytest.raises(RuntimeError) as raised:
        AplayPlayback("hw:X", sample_rate=16000, channels=2, sample_width=2).play(audio)
    message = str(raised.value)
    assert "22050" in message and "16000" in message, message


@pytest.mark.parametrize("rate,channels,width", [(16000, 1, 2), (16000, 2, 1), (48000, 2, 2)])
def test_each_mismatched_dimension_is_caught(tmp_path, rate, channels, width):
    audio = write_wav(tmp_path / "x.wav", rate=rate, channels=channels, width=width)
    with patch("subprocess.Popen") as popen, pytest.raises(RuntimeError, match="does not match"):
        AplayPlayback("hw:X", sample_rate=16000, channels=2, sample_width=2).play(audio)
    assert not popen.called


def test_an_explicit_opt_out_allows_any_format_where_the_device_converts(tmp_path):
    """Only for a path known to convert; the flag exists so the choice is visible."""
    audio = write_wav(tmp_path / "lessac.wav", rate=22050, channels=1, width=2)
    with patch("subprocess.Popen", return_value=FakeProcess()) as popen:
        AplayPlayback("hw:X", accept_any_format=True).play(audio).wait()
    assert popen.called


def test_unconstrained_expected_values_do_not_refuse(tmp_path):
    audio = write_wav(tmp_path / "x.wav", rate=22050, channels=1, width=2)
    with patch("subprocess.Popen", return_value=FakeProcess()):
        AplayPlayback("hw:X", sample_rate=None, channels=None, sample_width=None).play(audio).wait()


def test_a_non_wav_file_is_refused_before_any_process_starts(tmp_path):
    broken = tmp_path / "not-a-wav.wav"
    broken.write_bytes(b"not audio at all")
    with patch("subprocess.Popen") as popen, pytest.raises(RuntimeError, match="not a readable WAV"):
        AplayPlayback("hw:X").play(broken)
    assert not popen.called


def test_a_missing_file_is_refused_before_any_process_starts(tmp_path):
    with patch("subprocess.Popen") as popen, pytest.raises(RuntimeError, match="unreadable"):
        AplayPlayback("hw:X").play(tmp_path / "absent.wav")
    assert not popen.called


def test_an_empty_but_valid_wav_is_refused(tmp_path):
    audio = write_wav(tmp_path / "silent.wav", frames=0)
    with patch("subprocess.Popen") as popen, pytest.raises(RuntimeError, match="no audio frames"):
        AplayPlayback("hw:X").play(audio)
    assert not popen.called


def test_an_empty_device_is_rejected_at_construction():
    with pytest.raises(ValueError, match="ALSA device"):
        AplayPlayback("")


# --- failure and cancellation -------------------------------------------

def test_a_failed_player_is_reported_not_swallowed(tmp_path):
    audio = write_wav(tmp_path / "answer.wav")
    process = FakeProcess(returncode=1, stderr=b"aplay: audio open error: No such device")
    with patch("subprocess.Popen", return_value=process):
        handle = AplayPlayback("hw:X").play(audio)
        with pytest.raises(RuntimeError, match="aplay failed"):
            handle.wait()


def test_the_failure_detail_is_carried_into_the_error(tmp_path):
    audio = write_wav(tmp_path / "answer.wav")
    process = FakeProcess(returncode=1, stderr=b"audio open error: No such device")
    with patch("subprocess.Popen", return_value=process):
        with pytest.raises(RuntimeError, match="No such device"):
            AplayPlayback("hw:X").play(audio).wait()


def test_a_stopped_playback_does_not_report_the_termination_as_a_player_failure(tmp_path):
    """`stop` ends the process, so its non-zero code is expected, not an error."""
    audio = write_wav(tmp_path / "answer.wav")
    process = FakeProcess()
    with patch("subprocess.Popen", return_value=process):
        handle = AplayPlayback("hw:X").play(audio)
        handle.stop()
        handle.wait()
    assert handle.stopped is True
    assert process.terminated is True


def test_stop_reaps_the_process_so_silence_is_confirmed_not_assumed(tmp_path):
    audio = write_wav(tmp_path / "answer.wav")
    process = FakeProcess()
    with patch("subprocess.Popen", return_value=process):
        handle = AplayPlayback("hw:X").play(audio)
        handle.stop()
    assert process.waited >= 1, "stop must wait for the process, not merely signal it"


def test_stop_does_not_signal_a_process_that_already_ended(tmp_path):
    audio = write_wav(tmp_path / "answer.wav")
    process = FakeProcess(running=False)
    with patch("subprocess.Popen", return_value=process):
        AplayPlayback("hw:X").play(audio).stop()
    assert process.terminated is False


def test_an_unresponsive_player_is_killed_rather_than_left_sounding(tmp_path):
    audio = write_wav(tmp_path / "answer.wav")
    process = FakeProcess()
    calls = {"n": 0}

    def stubborn_wait(timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="aplay", timeout=timeout)
        process._running = False
        return -9

    process.wait = stubborn_wait
    with patch("subprocess.Popen", return_value=process):
        handle = AplayPlayback("hw:X").play(audio)
        handle.stop()
    assert process.killed is True


# --- and the conversion that makes Piper's output playable --------------

def test_a_resampled_piper_file_passes_the_format_check(tmp_path):
    """The two modules must agree, or the loop still refuses to speak.

    Piper emits 22050 Hz and C's device accepts 16000 only, so the adapter's
    format check is *supposed* to refuse Piper's raw output. This asserts both
    halves of that: refused before conversion, accepted after.
    """
    from sage_lantern.resample import resample_wav

    piper = tmp_path / "piper.wav"
    write_wav(piper, rate=22050, channels=2, width=2, frames=2205)
    player = AplayPlayback("hw:CARD=rockchipes8388,DEV=0")

    with pytest.raises(RuntimeError, match="does not match the device path"):
        player.play(piper)

    device = tmp_path / "device.wav"
    report = resample_wav(piper, device, target_rate=16000)
    assert report["target_rate"] == 16000

    process = FakeProcess()
    with patch("subprocess.Popen", return_value=process) as popen:
        player.play(device).wait()
    assert popen.called, "the converted file must be accepted"
    assert "hw:CARD=rockchipes8388,DEV=0" in popen.call_args[0][0]


def test_the_conversion_preserves_duration_so_the_speaker_does_not_race(tmp_path):
    """A wrong rate is a pitch *and* a duration error; duration is measurable here."""
    from sage_lantern.resample import resample_wav

    piper = tmp_path / "piper.wav"
    write_wav(piper, rate=22050, channels=2, width=2, frames=22050)  # one second
    device = tmp_path / "device.wav"
    resample_wav(piper, device, target_rate=16000)
    import wave as _wave
    with _wave.open(str(device), "rb") as stream:
        seconds = stream.getnframes() / stream.getframerate()
    assert seconds == pytest.approx(1.0, abs=1e-3)


# --- target_rate: the conversion is opt-in, not a silent side effect ----

def test_without_target_rate_a_mismatch_is_still_refused(tmp_path):
    """The old behaviour must survive: silence is not permission to convert."""
    piper = write_wav(tmp_path / "piper.wav", rate=22050, channels=2, width=2, frames=2205)
    player = AplayPlayback("hw:4,0")
    with pytest.raises(RuntimeError, match="does not match the device path"):
        player.play(piper)


def test_with_target_rate_a_mismatch_is_converted_and_played(tmp_path):
    piper = write_wav(tmp_path / "piper.wav", rate=22050, channels=2, width=2, frames=2205)
    player = AplayPlayback("hw:4,0", target_rate=16000)
    process = FakeProcess()
    with patch("subprocess.Popen", return_value=process) as popen:
        handle = player.play(piper)
    played = Path(popen.call_args[0][0][-1])
    assert played != piper, "the original must not be handed to aplay unchanged"
    with wave.open(str(played), "rb") as stream:
        assert stream.getframerate() == 16000
    handle.wait()


def test_a_file_already_at_the_target_rate_is_not_converted(tmp_path):
    """Converting a correct file would cost a temporary for no reason."""
    good = write_wav(tmp_path / "good.wav", rate=16000, channels=2, width=2, frames=1600)
    player = AplayPlayback("hw:4,0", target_rate=16000)
    with patch("subprocess.Popen", return_value=FakeProcess()) as popen:
        handle = player.play(good)
    assert popen.call_args[0][0][-1] == str(good), "the original file should be played"
    assert handle.temporary is None
    handle.wait()


def test_the_conversion_temporary_is_removed_after_playback(tmp_path):
    """Otherwise every spoken answer leaves a file behind on the unit."""
    piper = write_wav(tmp_path / "piper.wav", rate=22050, channels=2, width=2, frames=2205)
    player = AplayPlayback("hw:4,0", target_rate=16000)
    with patch("subprocess.Popen", return_value=FakeProcess()):
        handle = player.play(piper)
    temporary = handle.temporary
    assert temporary is not None and temporary.exists()
    handle.wait()
    assert not temporary.exists(), "the temporary must be gone once the sound ended"
    assert handle.temporary is None


def test_stopping_playback_also_removes_the_temporary(tmp_path):
    """Cancellation is the path most likely to leak, since wait() never runs."""
    piper = write_wav(tmp_path / "piper.wav", rate=22050, channels=2, width=2, frames=2205)
    player = AplayPlayback("hw:4,0", target_rate=16000)
    with patch("subprocess.Popen", return_value=FakeProcess()):
        handle = player.play(piper)
    temporary = handle.temporary
    assert temporary is not None and temporary.exists()
    handle.stop()
    assert not temporary.exists()
    assert handle.temporary is None


def test_the_original_recording_is_never_modified_or_removed(tmp_path):
    """The resampler writes a new file; the answer's own audio must survive."""
    piper = write_wav(tmp_path / "piper.wav", rate=22050, channels=2, width=2, frames=2205)
    before = piper.read_bytes()
    player = AplayPlayback("hw:4,0", target_rate=16000)
    with patch("subprocess.Popen", return_value=FakeProcess()):
        handle = player.play(piper)
    handle.wait()
    assert piper.read_bytes() == before


def test_a_failed_conversion_leaves_no_file_behind(tmp_path):
    """A refusal must not litter the speaking directory."""
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"not audio")
    player = AplayPlayback("hw:4,0", target_rate=16000)
    with pytest.raises(RuntimeError, match="not a readable WAV"):
        player.play(broken)
    assert list(tmp_path.iterdir()) == [broken], "a conversion temporary leaked"


def test_a_player_that_cannot_start_does_not_leak_its_temporary(tmp_path):
    piper = write_wav(tmp_path / "piper.wav", rate=22050, channels=2, width=2, frames=2205)
    player = AplayPlayback("hw:4,0", target_rate=16000)
    with patch("subprocess.Popen", side_effect=OSError("no aplay")):
        with pytest.raises(OSError):
            player.play(piper)
    assert list(tmp_path.iterdir()) == [piper], "a conversion temporary leaked"


def test_a_refused_conversion_still_refuses_when_the_rate_matches(tmp_path):
    """target_rate must not become a blanket acceptance of any format.

    The discriminator here is deliberately a DOWNMIX, not a mono file. Mono is
    convertible now (Piper writes mono and this device path is stereo), but a
    4-channel file cannot become stereo without deciding which two channels win,
    and that is a decision a silent conversion must not make on the caller's
    behalf. So this keeps the original principle - conversion removes a
    representational mismatch, it does not paper over a data mismatch.
    """
    wrong = write_wav(tmp_path / "wrong.wav", rate=16000, channels=4, width=2, frames=1600)
    player = AplayPlayback("hw:4,0", sample_rate=16000, channels=2, target_rate=16000)
    # Either layer may be the one to refuse, and both are correct: the resampler
    # raises ResampleError (a ValueError) because a downmix needs a rule it does
    # not have, and the format check raises RuntimeError. Which one fires depends
    # only on whether the conversion is attempted first, so asserting on the exact
    # type would pin an implementation detail rather than the guarantee.
    with pytest.raises((RuntimeError, ValueError), match="cannot_convert_channels|device expects"):
        player.play(wrong)


def test_a_mono_piper_file_is_widened_to_the_stereo_device(tmp_path):
    """The real end-to-end gap: Piper writes MONO, this device path is STEREO.

    Converting only the rate left a mono file that the format check then refused,
    so the voice loop could never have spoken no matter how the rate was set. And
    note the rate already matches here, so this also proves a layout change is
    still work rather than being short-circuited as "nothing to do".
    """
    mono = write_wav(tmp_path / "piper.wav", rate=16000, channels=1, width=2, frames=1600)
    player = AplayPlayback("hw:4,0", target_rate=16000)
    with patch("subprocess.Popen", return_value=FakeProcess()) as popen:
        handle = player.play(mono)
    played = Path(popen.call_args[0][0][-1])
    assert played != mono
    with wave.open(str(played), "rb") as stream:
        assert stream.getnchannels() == 2
        assert stream.getframerate() == 16000
        # Every source sample must survive on both sides - widening duplicates,
        # it does not invent or drop audio.
        frames = stream.readframes(stream.getnframes())
    original = wave.open(str(mono), "rb")
    source = original.readframes(original.getnframes())
    assert frames[0::4] == source[0::2]
    assert frames[2::4] == source[0::2]
    handle.wait()


def test_a_rate_and_layout_change_happen_together(tmp_path):
    """Piper's actual output: 22050 Hz mono into a 16000 Hz stereo device."""
    piper = write_wav(tmp_path / "piper22050.wav", rate=22050, channels=1,
                      width=2, frames=2205)
    player = AplayPlayback("hw:4,0", target_rate=16000)
    with patch("subprocess.Popen", return_value=FakeProcess()) as popen:
        handle = player.play(piper)
    played = Path(popen.call_args[0][0][-1])
    with wave.open(str(played), "rb") as stream:
        assert stream.getnchannels() == 2
        assert stream.getframerate() == 16000
    handle.wait()


@pytest.mark.parametrize("bad", [0, -1, 1.5, "16000", None])
def test_a_nonsense_target_rate_is_refused_at_construction(bad):
    if bad is None:
        # None is the meaningful "do not convert" default, not a nonsense value.
        assert AplayPlayback("hw:4,0", target_rate=None).target_rate is None
        return
    with pytest.raises(ValueError, match="positive integer"):
        AplayPlayback("hw:4,0", target_rate=bad)


# --- the path type the public API accepts -------------------------------

def test_a_string_path_plays_exactly_like_a_path_object(tmp_path):
    """Found on unit C, not in a test: the resampling branch used `.parent`, so a
    plain string raised `AttributeError: 'str' object has no attribute 'parent'`
    while the format checks — which call str() — were perfectly happy. The class
    accepted a string before `target_rate` existed, so it must go on accepting
    one; this pins that so the narrowing cannot recur.
    """
    good = write_wav(tmp_path / "good.wav", rate=16000, channels=2, width=2, frames=1600)
    player = AplayPlayback("hw:4,0", target_rate=16000)
    with patch("subprocess.Popen", return_value=FakeProcess()) as popen:
        handle = player.play(str(good))
    assert popen.call_args[0][0][-1] == str(good)
    handle.wait()


def test_a_string_path_is_converted_when_the_rate_differs(tmp_path):
    piper = write_wav(tmp_path / "piper.wav", rate=22050, channels=2, width=2, frames=2205)
    player = AplayPlayback("hw:4,0", target_rate=16000)
    with patch("subprocess.Popen", return_value=FakeProcess()) as popen:
        handle = player.play(str(piper))
    played = Path(popen.call_args[0][0][-1])
    assert played != piper
    with wave.open(str(played), "rb") as stream:
        assert stream.getframerate() == 16000
    handle.wait()


def test_a_refusal_still_happens_for_a_string_path(tmp_path):
    """The tolerant input must not weaken the format guarantee."""
    piper = write_wav(tmp_path / "piper.wav", rate=22050, channels=2, width=2, frames=2205)
    with pytest.raises(RuntimeError, match="does not match the device path"):
        AplayPlayback("hw:4,0").play(str(piper))
