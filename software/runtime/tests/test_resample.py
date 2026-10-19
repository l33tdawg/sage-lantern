"""The 22050 -> 16000 conversion unit C's hardware requires.

Round 9 measured on the device that C's audio output accepts 16000 Hz only and that
`hw:4,0` refuses 22050 outright, while Piper emits 22050. `sox` and `ffmpeg` are both
absent from the unit, so this conversion has to exist in-process.

No audio device is opened and no file outside `tmp_path` is touched except where a
test writes its own fixture. This is synthetic evidence about arithmetic, not about
a speaker.
"""

import array
import math
import re
import struct
import sys
import wave
from pathlib import Path

import pytest

from sage_lantern.resample import (
    to_mono_pcm,
    MAX_CHANNELS,
    MAX_RATE,
    MIN_RATE,
    ResampleError,
    resample_pcm,
    resample_wav,
)


def pcm(frames, width=2):
    """Interleave frames into little-endian PCM.

    Accepts bare ints (mono) or tuples/lists (one value per channel). Note that
    `(value,) * 1` is a bare int rather than a tuple, so this normalises the frame
    explicitly instead of trusting the caller's shape.
    """
    body = bytearray()
    for frame in frames:
        values = tuple(frame) if isinstance(frame, (tuple, list)) else (frame,)
        for value in values:
            if width == 2:
                body += struct.pack("<h", value)
            elif width == 1:
                body += struct.pack("<B", value)
            else:
                raise NotImplementedError(width)
    return bytes(body)


def unpack(raw, width=2):
    """The samples in raw PCM, as ints."""
    if width == 2:
        return list(struct.unpack("<%dh" % (len(raw) // 2), raw))
    return list(raw)


def sine_frames(count, rate, *, freq=440.0, amplitude=12000, channels=1):
    frames = []
    for index in range(count):
        value = int(amplitude * math.sin(2 * math.pi * freq * index / rate))
        frames.append(tuple([value] * channels))
    return frames


def write_wav(path: Path, frames, *, rate, channels, width=2) -> Path:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(rate)
        body = bytearray()
        for frame in frames:
            for value in frame:
                if width == 2:
                    body += struct.pack("<h", value)
                else:
                    body += struct.pack("<B", value)
        stream.writeframes(bytes(body))
    return path


# --- the conversion -----------------------------------------------------

def test_the_frame_count_follows_the_rate_ratio():
    frames = sine_frames(22050, 22050)
    out = resample_pcm(pcm(frames), width=2, channels=1,
                       source_rate=22050, target_rate=16000)
    # 22050 * (16000/22050) = 16000
    assert len(out) // 2 == 16000


def test_one_second_of_22050_becomes_one_second_of_16000():
    """Duration is what must be preserved; a pitch shift is a duration error."""
    frames = sine_frames(22050, 22050)
    out = resample_pcm(pcm(frames), width=2, channels=1,
                       source_rate=22050, target_rate=16000)
    assert len(out) // 2 / 16000 == pytest.approx(1.0, abs=1e-3)


def test_a_same_rate_request_is_a_pass_through():
    raw = pcm(sine_frames(1000, 16000))
    assert resample_pcm(raw, width=2, channels=1,
                        source_rate=16000, target_rate=16000) == raw


@pytest.mark.parametrize("source,target", [(22050, 16000), (16000, 22050), (48000, 16000),
                                           (44100, 16000), (22050, 48000)])
def test_every_common_conversion_produces_whole_frames(source, target):
    frames = sine_frames(source // 4, source)  # a quarter second
    out = resample_pcm(pcm(frames), width=2, channels=1,
                       source_rate=source, target_rate=target)
    assert len(out) % 2 == 0
    expected = int(source // 4 * target / source)
    assert abs(len(out) // 2 - expected) <= 1


# --- the invariants that catch arithmetic mistakes ----------------------

def test_a_constant_signal_stays_constant():
    """A resampler that mis-weights its edges will ripple a flat input."""
    frames = [(1000,)] * 3000
    out = resample_pcm(pcm(frames), width=2, channels=1,
                       source_rate=22050, target_rate=16000)
    values = unpack(out)
    assert set(values) == {1000}, sorted(set(values))[:5]


@pytest.mark.parametrize("level", [-32768, -20000, -1, 0, 1, 20000, 32767])
def test_a_constant_at_any_level_stays_that_level(level):
    frames = [(level,)] * 1500
    out = resample_pcm(pcm(frames), width=2, channels=1,
                       source_rate=22050, target_rate=16000)
    values = unpack(out)
    assert set(values) == {level}


def test_silence_stays_silent():
    out = resample_pcm(pcm([(0,)] * 2000), width=2, channels=1,
                       source_rate=22050, target_rate=16000)
    assert set(unpack(out)) == {0}


def test_a_ramp_is_monotonic_so_the_signal_is_not_reordered():
    frames = [(index - 16000,) for index in range(32000)]
    out = resample_pcm(pcm(frames), width=2, channels=1,
                       source_rate=22050, target_rate=16000)
    values = unpack(out)
    assert values == sorted(values), "a monotonic input must not be reordered"


def test_dc_offset_is_preserved_not_drifted():
    frames = [(5000,)] * 4410
    out = resample_pcm(pcm(frames), width=2, channels=1,
                       source_rate=22050, target_rate=16000)
    values = unpack(out)
    assert all(value == 5000 for value in values)


# --- channels and widths ------------------------------------------------

def test_stereo_channels_are_converted_independently():
    left = [1000] * 2205
    right = [-1000] * 2205
    interleaved = b"".join(struct.pack("<hh", a, b) for a, b in zip(left, right))
    out = resample_pcm(interleaved, width=2, channels=2,
                       source_rate=22050, target_rate=16000)
    values = unpack(out)
    assert set(values[0::2]) == {1000}
    assert set(values[1::2]) == {-1000}


def test_unsigned_8_bit_is_converted_within_its_range():
    frames = [(128,)] * 1000
    out = resample_pcm(pcm(frames, width=1), width=1, channels=1,
                       source_rate=22050, target_rate=16000)
    assert set(out) == {128}


def test_clipping_keeps_samples_inside_the_width():
    """Averaging can overshoot mid-frame; the output must still be valid PCM."""
    frames = [(-32768,), (32767,)] * 1000
    out = resample_pcm(pcm(frames), width=2, channels=1,
                       source_rate=22050, target_rate=16000)
    values = unpack(out)
    assert all(-32768 <= value <= 32767 for value in values)


def test_the_same_input_gives_the_same_output_twice():
    """Integer arithmetic: reproducibility is a property, not a hope."""
    raw = pcm(sine_frames(2205, 22050))
    first = resample_pcm(raw, width=2, channels=1, source_rate=22050, target_rate=16000)
    second = resample_pcm(raw, width=2, channels=1, source_rate=22050, target_rate=16000)
    assert first == second


# --- refusals -----------------------------------------------------------

@pytest.mark.parametrize("source,target", [(0, 16000), (16000, 0), (7999, 16000),
                                           (22050, 192001), (-1, 16000), (22050, 1.5)])
def test_impossible_rates_are_refused(source, target):
    with pytest.raises(ResampleError):
        resample_pcm(b"\x00\x00", width=2, channels=1,
                     source_rate=source, target_rate=target)


@pytest.mark.parametrize("width", [0, 5, 8, -2, "2", None])
def test_unsupported_sample_widths_are_refused(width):
    with pytest.raises(ResampleError, match="unsupported_sample_width"):
        resample_pcm(b"\x00" * 16, width=width, channels=1,
                     source_rate=22050, target_rate=16000)


@pytest.mark.parametrize("channels", [0, MAX_CHANNELS + 1, -1, "2", None])
def test_unsupported_channel_counts_are_refused(channels):
    with pytest.raises(ResampleError, match="unsupported_channel_count"):
        resample_pcm(b"\x00" * 16, width=2, channels=channels,
                     source_rate=22050, target_rate=16000)


def test_a_partial_frame_is_refused():
    with pytest.raises(ResampleError, match="frames_not_whole"):
        resample_pcm(b"\x00\x00\x00", width=2, channels=1,
                     source_rate=22050, target_rate=16000)


def test_the_rate_bounds_are_the_documented_ones():
    assert (MIN_RATE, MAX_RATE, MAX_CHANNELS) == (8000, 192000, 8)


# --- the file-level API -------------------------------------------------

def test_a_wav_is_converted_and_reports_what_it_did(tmp_path):
    source = write_wav(tmp_path / "piper.wav", sine_frames(22050, 22050),
                       rate=22050, channels=1)
    report = resample_wav(source, tmp_path / "device.wav", target_rate=16000)
    assert report["source_rate"] == 22050
    assert report["target_rate"] == 16000
    assert report["channels"] == 1
    assert report["source_frames"] == 22050
    assert report["target_frames"] == 16000
    with wave.open(str(tmp_path / "device.wav"), "rb") as stream:
        assert stream.getframerate() == 16000
        assert stream.getnchannels() == 1
        assert stream.getnframes() == 16000


def test_the_converted_file_is_something_aplay_accepts(tmp_path):
    """The header must describe the target rate, or the device still refuses."""
    source = write_wav(tmp_path / "a.wav", sine_frames(4000, 22050, channels=2),
                       rate=22050, channels=2)
    resample_wav(source, tmp_path / "b.wav", target_rate=16000)
    with wave.open(str(tmp_path / "b.wav"), "rb") as stream:
        assert (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) == (16000, 2, 2)


def test_a_missing_source_is_refused_and_nothing_is_written(tmp_path):
    target = tmp_path / "out.wav"
    with pytest.raises(ResampleError, match="unreadable_source"):
        resample_wav(tmp_path / "absent.wav", target, target_rate=16000)
    assert not target.exists(), "a refusal must not leave a half-written file"


def test_a_non_wav_source_is_refused_and_nothing_is_written(tmp_path):
    broken = tmp_path / "not.wav"
    broken.write_bytes(b"not audio")
    target = tmp_path / "out.wav"
    with pytest.raises(ResampleError, match="unreadable_source"):
        resample_wav(broken, target, target_rate=16000)
    assert not target.exists()


def test_an_empty_wav_is_refused(tmp_path):
    empty = write_wav(tmp_path / "empty.wav", [], rate=22050, channels=1)
    with pytest.raises(ResampleError, match="source_has_no_frames"):
        resample_wav(empty, tmp_path / "out.wav", target_rate=16000)


def test_a_refusal_does_not_replace_an_existing_target(tmp_path):
    """The caller's previous audio must survive a failed conversion."""
    good = tmp_path / "good.wav"
    good.write_bytes(b"PRECIOUS")
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"not audio")
    with pytest.raises(ResampleError):
        resample_wav(broken, good, target_rate=16000)
    assert good.read_bytes() == b"PRECIOUS"


def test_the_module_needs_no_deprecated_audio_module():
    """`audioop` was removed in Python 3.13; the package supports 3.11+."""
    source = Path(__file__).resolve().parents[1] / "src/sage_lantern/resample.py"
    text = source.read_text()
    assert not re.search(r"^\s*(import|from)\s+audioop", text, re.MULTILINE)
    assert sys.version_info >= (3, 11)


# --- the widths that are claimed but not otherwise exercised -----------

@pytest.mark.parametrize("width", [3, 4])
def test_24_and_32_bit_pcm_are_converted_and_stay_in_range(width):
    """3- and 4-byte samples take a different path from 1 and 2, so exercise them."""
    bits = width * 8
    high = (1 << (bits - 1)) - 1
    value = high // 2
    frames = [value] * 2000
    raw = bytearray()
    for frame in frames:
        raw += (frame & ((1 << bits) - 1)).to_bytes(width, "little", signed=False)
    out = resample_pcm(bytes(raw), width=width, channels=1,
                       source_rate=22050, target_rate=16000)
    assert len(out) % width == 0
    checked = []
    for offset in range(0, len(out), width):
        item = int.from_bytes(out[offset:offset + width], "little", signed=False)
        if item >= 1 << (bits - 1):
            item -= 1 << bits
        checked.append(item)
    assert set(checked) == {value}, sorted(set(checked))[:4]


# --- mono downmix for speech recognition --------------------------------

def test_mono_downmix_passes_mono_through_unchanged():
    raw = array.array("h", [10, -20, 30, -40]).tobytes()
    assert to_mono_pcm(raw, width=2, channels=1) == raw


def test_mono_keeps_the_first_channel_verbatim():
    raw = array.array("h", [100, 200, -100, -200]).tobytes()
    out = array.array("h")
    out.frombytes(to_mono_pcm(raw, width=2, channels=2))
    assert list(out) == [100, -100]


def test_mono_survives_an_anti_correlated_capture():
    """The real defect, as a test.

    Unit C's ES8323 capture is differential, so its channels are the same signal
    with opposite sign (measured correlation -0.9920). Averaging them cancels the
    signal, which is what silently discarded every spoken question. Selecting a
    channel must preserve it, and this is the invariant that matters: the output
    has to carry the audio, not merely be the right length.

    On that capture the SECOND channel is the one to keep: over 20 phrases spoken
    by four different voices it scored mean word error rate 0.146 against 0.358,
    with 13/20 exact against 5/20. Level, DC offset and polarity were each ruled
    out as the cause on the unit - negating the first channel, scaling it to the
    second's level and removing its DC all reproduce the first channel's
    transcript exactly - so this pins the measured choice rather than a theory.
    """
    left = [1000, -2000, 3000, -4000]
    right = [-value for value in left]
    raw = array.array("h", [v for pair in zip(left, right) for v in pair]).tobytes()
    out = array.array("h")
    out.frombytes(to_mono_pcm(raw, width=2, channels=2))
    assert list(out) == right
    assert any(value != 0 for value in out), "an averaged downmix would be all zeros here"


def test_mono_keeps_the_first_channel_of_an_ordinary_stereo_pair():
    """Anti-correlation is the exception, not the rule.

    A normal stereo pair keeps the behaviour it always had, which is also what
    keeps a silent or dead second channel from turning a working capture into
    silence.
    """
    left = [1000, -2000, 3000, -4000]
    right = [value // 2 for value in left]
    raw = array.array("h", [v for pair in zip(left, right) for v in pair]).tobytes()
    out = array.array("h")
    out.frombytes(to_mono_pcm(raw, width=2, channels=2))
    assert list(out) == left


def test_mono_keeps_the_first_channel_when_the_second_is_silent():
    left = [1000, -2000, 3000, -4000]
    right = [0, 0, 0, 0]
    raw = array.array("h", [v for pair in zip(left, right) for v in pair]).tobytes()
    out = array.array("h")
    out.frombytes(to_mono_pcm(raw, width=2, channels=2))
    assert list(out) == left


def test_mono_keeps_the_first_channel_of_a_weakly_anti_correlated_pair():
    """The threshold is a sign test, not a hair trigger.

    This pair's correlation is about -0.06: related enough that it is plainly not
    a differential capture, so the ordinary rule applies.
    """
    left = [1000, -1000, 1000, -1000]
    right = [-800, 1000, 600, -1000]
    raw = array.array("h", [v for pair in zip(left, right) for v in pair]).tobytes()
    out = array.array("h")
    out.frombytes(to_mono_pcm(raw, width=2, channels=2))
    assert list(out) == left


def test_mono_does_not_overflow_at_full_scale():
    raw = array.array("h", [32767, 32767, -32768, -32768]).tobytes()
    out = array.array("h")
    out.frombytes(to_mono_pcm(raw, width=2, channels=2))
    assert all(-32768 <= value <= 32767 for value in out)
    assert list(out) == [32767, -32768]


def test_mono_downmix_refuses_a_partial_frame():
    with pytest.raises(ResampleError):
        to_mono_pcm(b"\x01\x02\x03", width=2, channels=2)


def test_mono_downmix_refuses_nonsense_shape():
    with pytest.raises(ResampleError):
        to_mono_pcm(b"\x00\x00", width=2, channels=0)


# --- output length: a zero-padding regression ---------------------------

@pytest.mark.parametrize("width", [1, 2, 4])
@pytest.mark.parametrize("channels", [1, 2])
def test_interleave_emits_exactly_the_frames_it_was_given(width, channels):
    """Every converted file must contain exactly as many frames as it should.

    This is the regression for a real defect: `_interleave` sized its buffer with
    `bytes(n)`, and an array type code reinterprets that buffer, so the array came
    out `width` times too long and everything past the written part stayed zero.
    At 16-bit that silently replaced the back half of every converted file with
    silence. Short test signals padded by a sample or two, so nothing caught it -
    the length assertion is what makes the class of bug visible, which is why this
    checks the count rather than the samples.
    """
    frames = 1000
    # Width 1 is an UNSIGNED byte, so the probe values have to fit it - the point
    # of this test is the output length, not the sample range.
    offset = 128 if width == 1 else 100
    values = [((i * 37) % 200 - 100) + offset for i in range(frames * channels)]
    raw = array.array({1: "B", 2: "h", 4: "i"}[width], values).tobytes()
    if channels == 1:
        out = to_mono_pcm(raw, width=width, channels=1)
    else:
        out = to_mono_pcm(raw, width=width, channels=channels)
    assert len(out) == width * frames


def test_resampling_does_not_pad_the_tail_with_silence():
    """The same bug reached the rate converter, so pin it there too.

    A constant signal is the right probe: it must stay constant all the way to
    the last frame, and padding would show up as a run of zeros at the end.
    """
    frames = 22050
    raw = array.array("h", [1000] * (frames * 2)).tobytes()
    out = resample_pcm(raw, width=2, channels=2, source_rate=22050, target_rate=16000)
    values = array.array("h")
    values.frombytes(out)
    expected = frames * 16000 // 22050
    assert len(values) == expected * 2
    assert set(values) == {1000}, "a zero-padded tail would introduce values other than 1000"
